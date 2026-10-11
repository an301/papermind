"""Generate the writer's training data: distil Claude's level-calibrated answers.

For each usable passage of each training paper:

1. Claude writes a question a reader might ask that the passage answers, and
   the short plan the agent would hand the writer (one structured call).
2. The real ClaudeWriter answers that question three times, at beginner,
   intermediate and expert, from the same sources.

Each answer is stored with the exact prompt the fine-tuned writer will see
in production (``mistral_messages``), so training and serving can't drift.
The three answers to one question also give the DPO pairs for free: the right
level is "chosen", a fluent and correct answer at the wrong level is "rejected".

    .venv/bin/python -m training.generate --ingest            # fetch the 30 papers (no API spend)
    .venv/bin/python -m training.generate --pilot             # 2 papers x 3 passages, about $1
    .venv/bin/python -m training.generate --max-cost 60       # the full run, resumable

Resumable: rows already in raw.jsonl are skipped, so a stopped run picks up
where it left off. --max-cost stops cleanly once the spend passes the cap.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass

from pydantic import BaseModel, Field

from papermind.agent.labels import label_sources
from papermind.agent.writer import ClaudeWriter, WriterInput, mistral_messages
from papermind.config import get_settings
from papermind.ingest.pipeline import Ingestor
from papermind.ingest.store import PaperStore
from papermind.llm.client import LLM, CallResult, PaperMindLLMError
from papermind.schemas.paper import Chunk, PaperRecord
from papermind.schemas.tools import Level, RetrievedChunk
from training.common import LEVELS, PAPERS_DIR, RAW_PATH, read_jsonl, training_paper_ids

log = logging.getLogger("training.generate")

MIN_CHARS = 400  # fragments can't be explained at three levels
SKIP_SECTIONS = re.compile(r"reference|bibliograph|acknowledg|front matter|author contribution", re.I)

QUESTION_SYSTEM = """You write training data for a reading assistant that explains ML research papers.

You will get one passage from a paper. Write:
- question: something a real reader of this paper would ask that THIS passage answers. Phrase it the way a person types, not by copying the passage's wording. Vary the kind of question across calls: why, how, what does X mean, what's the intuition, how does it compare, what would happen if.
- plan: 2-4 sentences the research agent would hand the writer: what the answer must cover and in what order, without stating the reader's level (the writer gets that separately).
- concepts_to_scaffold: up to 3 ideas a reader would need first to follow the answer, as canonical ML names (may be empty).

The question must be answerable from the passage alone."""


class QuestionPlan(BaseModel):
    question: str = Field(min_length=8, max_length=300)
    plan: str = Field(min_length=10, max_length=800)
    concepts_to_scaffold: list[str] = Field(default_factory=list, max_length=3)


@dataclass
class Spend:
    usd: float = 0.0
    calls: int = 0

    def add(self, result: CallResult) -> None:
        self.usd += result.cost_usd
        self.calls += 1


def usable_chunks(record: PaperRecord, per_paper: int) -> list[Chunk]:
    """Passages long enough to explain three ways, spread evenly over the paper."""
    good = [
        c for c in record.chunks if len(c.text) >= MIN_CHARS and not SKIP_SECTIONS.search(c.section_title)
    ]
    if len(good) <= per_paper:
        return good
    step = len(good) / per_paper
    return [good[int(i * step)] for i in range(per_paper)]


def as_retrieved(chunk: Chunk, rank: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        section_id=chunk.section_id,
        section_number=chunk.section_number,
        section_title=chunk.section_title,
        text=chunk.text,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        rank=rank,
        score=1.0 / rank,
    )


def sources_for(record: PaperRecord, chunk: Chunk) -> list[RetrievedChunk]:
    """The passage as S1 and its neighbour as S2: in production the writer gets several
    passages and must cite the ones that support each claim, so training shows it two."""
    i = record.chunks.index(chunk)
    neighbour = next(
        (
            c
            for c in (record.chunks[i + 1 : i + 2] + record.chunks[max(0, i - 1) : i])
            if len(c.text) >= MIN_CHARS
        ),
        None,
    )
    out = [as_retrieved(chunk, 1)]
    if neighbour is not None and not SKIP_SECTIONS.search(neighbour.section_title):
        out.append(as_retrieved(neighbour, 2))
    return out


async def ingest_all() -> None:
    """Fetch the training papers into their own store. No API spend, no embeddings."""
    store = PaperStore(PAPERS_DIR)
    ingestor = Ingestor(store, embedder=None, cache=None)
    for arxiv_id in training_paper_ids():
        paper_id = arxiv_id.replace("/", "_")
        if store.meta(paper_id) is not None:
            print(f"have      {arxiv_id}")
            continue
        try:
            record = await ingestor.ingest_arxiv(arxiv_id)
            print(f"ingested  {arxiv_id}  {record.meta.title[:60]}  ({len(record.chunks)} chunks)")
        except Exception as exc:  # one bad PDF shouldn't stop the batch
            print(f"FAILED    {arxiv_id}: {exc}", file=sys.stderr)


async def question_for(llm: LLM, chunk: Chunk, title: str, spend: Spend, effort: str) -> QuestionPlan:
    result = await llm.parse(
        system=[{"type": "text", "text": QUESTION_SYSTEM}],
        messages=[
            {
                "role": "user",
                "content": f'<paper>{title}</paper>\n<passage section="{chunk.section_title}">\n{chunk.text}\n</passage>',
            }
        ],
        schema=QuestionPlan,
        effort=effort,
        max_tokens=2000,
    )
    spend.add(result)
    assert isinstance(result.parsed, QuestionPlan)
    return result.parsed


async def answer_at(writer: ClaudeWriter, inp: WriterInput) -> str:
    return "".join([piece async for piece in writer.stream(inp)])


async def generate_for_chunk(
    llm: LLM, writer: ClaudeWriter, record: PaperRecord, chunk: Chunk, spend: Spend, effort: str
) -> list[dict]:
    plan = await question_for(llm, chunk, record.meta.title, spend, effort)
    sources = label_sources(sources_for(record, chunk))
    rows: list[dict] = []

    async def one(level: Level) -> None:
        inp = WriterInput(
            question=plan.question,
            level=level,
            paper=record.meta,
            plan=plan.plan,
            altitude="technical" if level == "expert" else "scaffolded",
            sources=sources,
            concepts_to_scaffold=[] if level == "expert" else plan.concepts_to_scaffold,
            on_call=spend.add,
        )
        answer = await answer_at(writer, inp)
        rows.append(
            {
                "id": f"{chunk.chunk_id}:{level}",
                "group": chunk.chunk_id,
                "paper_id": record.meta.paper_id,
                "chunk_id": chunk.chunk_id,
                "level": level,
                "question": plan.question,
                "plan": plan.plan,
                "source_labels": [s.label for s in sources],
                "messages": mistral_messages(inp),
                "answer": answer,
                "writer": writer.name,
            }
        )

    # The three levels share the cached writer prompt; running them together keeps a run short.
    await asyncio.gather(*(one(level) for level in LEVELS))
    return sorted(rows, key=lambda r: LEVELS.index(r["level"]))


async def generate(args: argparse.Namespace) -> None:
    settings = get_settings()
    store = PaperStore(PAPERS_DIR)
    llm = LLM(settings)
    writer = ClaudeWriter(llm, settings)
    done = {row["group"] for row in read_jsonl(RAW_PATH)}
    spend = Spend()

    papers = training_paper_ids()
    if args.pilot:
        papers, args.per_paper = papers[:2], 3
    records = [r for pid in papers if (r := store.load(pid.replace("/", "_"))) is not None]
    if not records:
        raise SystemExit("No training papers ingested yet. Run with --ingest first.")

    todo = [(r, c) for r in records for c in usable_chunks(r, args.per_paper) if c.chunk_id not in done]
    print(f"{len(records)} papers, {len(todo)} passages to do ({len(done)} already in {RAW_PATH.name})")
    if args.dry_run:
        return

    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    gate = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()
    stop = asyncio.Event()

    async def work(record: PaperRecord, chunk: Chunk) -> None:
        async with gate:
            if stop.is_set():
                return
            try:
                rows = await generate_for_chunk(llm, writer, record, chunk, spend, settings.writer_effort)
            except PaperMindLLMError as exc:
                log.warning("skipped %s: %s", chunk.chunk_id, exc.message)
                if "out of credits" in exc.message:
                    stop.set()  # every remaining call would fail the same way
                return
            async with lock:
                with RAW_PATH.open("a", encoding="utf-8") as f:
                    for row in rows:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"  ${spend.usd:7.2f}  {chunk.chunk_id}")
            if spend.usd >= args.max_cost:
                stop.set()

    try:
        await asyncio.gather(*(work(r, c) for r, c in todo))
    finally:
        await llm.aclose()
    note = " (stopped at --max-cost; rerun to continue)" if stop.is_set() else ""
    print(f"done: {spend.calls} calls, ${spend.usd:.2f}{note}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ingest", action="store_true", help="fetch the training papers (no API spend)")
    p.add_argument("--pilot", action="store_true", help="2 papers x 3 passages, to check the output")
    p.add_argument("--per-paper", type=int, default=40, help="passages per paper (default 40)")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument(
        "--max-cost", type=float, default=5.0, help="stop once this many USD are spent (default 5)"
    )
    p.add_argument("--dry-run", action="store_true", help="count the work, spend nothing")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(ingest_all() if args.ingest else generate(args))


if __name__ == "__main__":
    main()
