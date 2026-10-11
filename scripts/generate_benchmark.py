"""Generate benchmark candidates backwards from chunks, through the API.

    .venv/bin/python scripts/generate_benchmark.py --papers 1706.03762 --per-paper 5 --dry-run
    .venv/bin/python scripts/generate_benchmark.py --per-paper 4            # spends money

Backwards means the chunk comes first. The script picks chunks (no References
or other back matter, nothing under 400 characters, spread across sections,
chunks already used as gold labels skipped by default), then asks the model
for a question that chunk answers, a target level and a reference answer. The
gold label costs nothing: it is the chunk the question was written from.

Candidates go to evals/results/ (gitignored), never straight into
evals/benchmark/questions.jsonl: generated questions are checked before they
are merged (see evals/benchmark/README.md). These papers are test data; do not
reuse anything produced here as training data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anthropic import transform_schema
from pydantic import BaseModel, Field

from evals.harness.dataset import (
    DEFAULT_BENCHMARK,
    LEVELS,
    QUERY_TYPES,
    RESULTS_DIR,
    BenchmarkRow,
    PaperLibrary,
    QueryType,
)
from papermind.config import REPO_ROOT, Settings, get_settings
from papermind.llm.client import FALLBACK_BETA, LLM, PaperMindLLMError
from papermind.schemas.paper import Chunk, PaperRecord
from papermind.schemas.tools import Level

PAPERS_FILE = REPO_ROOT / "evals" / "benchmark" / "papers.txt"
MIN_CHARS = 400
MAX_TOKENS = 16000
# Back matter answers no question a reader asks about the paper's content, and
# a front-matter chunk is a title page.
SKIPPED_SECTIONS = re.compile(
    r"^(references?|bibliography|acknowledge?ments?|author contributions?|front matter)$", re.IGNORECASE
)


class GeneratedQuestion(BaseModel):
    answerable_from_chunk_alone: bool = Field(
        description="true only if this one passage fully answers the question, without other parts of the paper"
    )
    question: str = Field(description="the question, phrased the way a reader at target_level would ask it")
    target_level: Level
    query_type: QueryType
    reference_answer: str = Field(
        description="2-4 sentences answering the question from the passage alone, at target_level"
    )


SYSTEM = """You write evaluation questions for PaperMind, an assistant that answers questions about machine-learning papers by retrieving passages and explaining them at the reader's level.

You are given one passage of a paper. Write one question that THIS passage answers, as a real reader of the paper would ask it, plus a reference answer.

Rules:
- The passage alone must answer the question. If another part of the paper (see the outline) would answer it just as well, ask something more specific to this passage.
- Write for the requested reader level, as that reader would phrase it:
  - beginner: plain words, no notation, curious about what and why.
  - intermediate: standard ML vocabulary and notation, asks how it works.
  - expert: precise and technical, asks about a specific design choice, setting or consequence.
  If the passage cannot support a question at that level, use the closest level it can support and set target_level to it.
- Prefer the suggested query type when it fits the passage: lookup (a specific setting or table value), numeric (a number or result), method (how something works), conceptual (why it works), motivation (what problem it addresses), comparison (X versus Y within the paper).
- Do not copy a sentence from the passage into the question.
- reference_answer: 2-4 sentences, faithful to the passage only. No outside facts, and every number exactly as the passage gives it.
- Set answerable_from_chunk_alone to false if you could not write a question that meets these rules."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--papers", nargs="+", help="paper ids (default: every paper in evals/benchmark/papers.txt)"
    )
    p.add_argument("--per-paper", type=int, default=5, help="questions to generate per paper")
    p.add_argument("--seed", type=int, default=13, help="chunk sampling seed")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default="high")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--min-chars", type=int, default=MIN_CHARS)
    p.add_argument("--include-used", action="store_true", help="allow chunks that are already gold labels")
    p.add_argument(
        "--benchmark", type=Path, default=DEFAULT_BENCHMARK, help="existing rows (ids, used chunks)"
    )
    p.add_argument(
        "--out", type=Path, help="output JSONL (default: evals/results/generated-<timestamp>.jsonl)"
    )
    p.add_argument(
        "--dry-run", action="store_true", help="print the first request and the chunk plan; no API call"
    )
    return p.parse_args(argv)


def benchmark_papers() -> list[str]:
    ids: list[str] = []
    for line in PAPERS_FILE.read_text(encoding="utf-8").splitlines():
        token = line.split("#", 1)[0].strip()
        if token:
            ids.append(re.sub(r"v\d+$", "", token))
    return ids


def existing_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def eligible(chunk: Chunk, min_chars: int) -> bool:
    return not SKIPPED_SECTIONS.match(chunk.section_title.strip()) and len(chunk.text) >= min_chars


def pick_chunks(
    record: PaperRecord, n: int, *, used: set[str], min_chars: int, rng: random.Random
) -> list[Chunk]:
    """``n`` eligible chunks spread across top-level sections: round-robin over sections, shuffled within each."""
    groups: dict[str, list[Chunk]] = {}
    for chunk in record.chunks:
        if eligible(chunk, min_chars) and chunk.chunk_id not in used:
            top = (chunk.section_number or chunk.section_id).split(".")[0]
            groups.setdefault(top, []).append(chunk)
    queues = list(groups.values())
    for queue in queues:
        rng.shuffle(queue)
    rng.shuffle(queues)
    picked: list[Chunk] = []
    while len(picked) < n and any(queues):
        for queue in queues:
            if queue and len(picked) < n:
                picked.append(queue.pop())
    order = {c.chunk_id: i for i, c in enumerate(record.chunks)}
    return sorted(picked, key=lambda c: order[c.chunk_id])


def outline(record: PaperRecord) -> str:
    return "\n".join(
        f"{'  ' * (s.level - 1)}{s.number + ' ' if s.number else ''}{s.title}" for s in record.meta.sections
    )


def user_message(record: PaperRecord, chunk: Chunk, level: Level, query_type: QueryType) -> str:
    section = f"{chunk.section_number} {chunk.section_title}" if chunk.section_number else chunk.section_title
    return "\n".join(
        [
            f"<paper>{record.meta.title}</paper>",
            "<outline>",
            outline(record),
            "</outline>",
            f'<passage section="{section}">',
            chunk.text.strip(),
            "</passage>",
            f"<requested_level>{level}</requested_level>",
            f"<suggested_query_type>{query_type}</suggested_query_type>",
        ]
    )


def request_preview(settings: Settings, effort: str, user: str) -> dict[str, Any]:
    """What ``LLM.parse`` sends for one chunk, with the output schema as the SDK renders it."""
    request: dict[str, Any] = {
        "model": settings.model,
        "max_tokens": MAX_TOKENS,
        "stream": True,
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": effort,
            "format": {
                "type": "json_schema",
                "schema": transform_schema(GeneratedQuestion.model_json_schema()),
            },
        },
        "system": [{"type": "text", "text": SYSTEM}],
        "messages": [{"role": "user", "content": user}],
    }
    if settings.enable_fallbacks:
        request["betas"] = [FALLBACK_BETA]
        request["fallbacks"] = "default"
    return request


def next_ids(paper_id: str, taken: set[str], n: int) -> list[str]:
    numbers = [int(m.group(1)) for t in taken if (m := re.fullmatch(rf"{re.escape(paper_id)}-g(\d+)", t))]
    start = max(numbers, default=0) + 1
    return [f"{paper_id}-g{start + i:02d}" for i in range(n)]


async def generate(args: argparse.Namespace) -> int:
    settings = get_settings()
    library = PaperLibrary(settings.papers_dir)
    papers = args.papers or benchmark_papers()
    rows = existing_rows(args.benchmark)
    used = set() if args.include_used else {r["gold_chunk"] for r in rows}
    taken = {r["id"] for r in rows}
    rng = random.Random(args.seed)

    jobs: list[tuple[str, PaperRecord, Chunk, Level, QueryType]] = []
    for p_index, paper_id in enumerate(papers):
        record = library.record(paper_id)
        if record is None:
            print(f"skipping {paper_id}: not ingested", file=sys.stderr)
            continue
        chunks = pick_chunks(record, args.per_paper, used=used, min_chars=args.min_chars, rng=rng)
        ids = next_ids(paper_id, taken, len(chunks))
        for i, (row_id, chunk) in enumerate(zip(ids, chunks, strict=True)):
            # Rotated per paper so small runs still cover every level and type.
            level = LEVELS[(p_index + i) % len(LEVELS)]
            query_type = QUERY_TYPES[(p_index + i) % len(QUERY_TYPES)]
            jobs.append((row_id, record, chunk, level, query_type))

    print(f"{len(jobs)} chunks from {len(papers)} papers:")
    for row_id, _, chunk, level, query_type in jobs:
        print(f"  {row_id}  {chunk.chunk_id}  ({len(chunk.text)} chars)  {level}/{query_type}")
    if not jobs:
        return 1
    if args.dry_run:
        row_id, record, chunk, level, query_type = jobs[0]
        print(f"\nRequest for {row_id} (each chunk sends one like it):")
        print(
            json.dumps(
                request_preview(settings, args.effort, user_message(record, chunk, level, query_type)),
                indent=2,
            )
        )
        return 0

    try:
        llm = LLM(settings)
    except PaperMindLLMError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    gate = asyncio.Semaphore(max(1, args.concurrency))
    cost = 0.0

    async def one(job: tuple[str, PaperRecord, Chunk, Level, QueryType]) -> BenchmarkRow | None:
        nonlocal cost
        row_id, record, chunk, level, query_type = job
        async with gate:
            try:
                result = await llm.parse(
                    system=[{"type": "text", "text": SYSTEM}],
                    messages=[{"role": "user", "content": user_message(record, chunk, level, query_type)}],
                    schema=GeneratedQuestion,
                    effort=args.effort,
                    max_tokens=MAX_TOKENS,
                )
            except PaperMindLLMError as exc:
                print(f"  {row_id}: {exc.code} {exc.message}", file=sys.stderr)
                return None
        cost += result.cost_usd
        q = result.parsed
        assert isinstance(q, GeneratedQuestion)
        if not q.answerable_from_chunk_alone:
            print(f"  {row_id}: model could not write a single-chunk question for {chunk.chunk_id}; dropped")
            return None
        return BenchmarkRow(
            id=row_id,
            paper_id=record.meta.paper_id,
            question=q.question.strip(),
            target_level=q.target_level,
            gold_chunk=chunk.chunk_id,
            reference_answer=q.reference_answer.strip(),
            origin="generated",
            query_type=q.query_type,
        )

    try:
        produced = await asyncio.gather(*(one(j) for j in jobs))
    finally:
        await llm.aclose()
    kept = [r for r in produced if r is not None]
    out = args.out or RESULTS_DIR / f"generated-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(r.model_dump_json() + "\n" for r in kept), encoding="utf-8")
    print(f"\nWrote {len(kept)}/{len(jobs)} candidates to {out} (${cost:.4f}). Check them before merging.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(generate(parse_args())))
