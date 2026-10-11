"""The writer suite: does the answer come out at the level the reader asked for?

The calibration experiment from the fine-tuning plan. Every writer gets the
same input for each benchmark row: the question, the target level, and the
gold passage as S1 (retrieval is held fixed, so only the writer varies).
The blind level judge labels each answer; calibration accuracy is the share
that matches the requested level. Faithfulness is measured too, because a
writer can sound simpler by inventing simpler-sounding claims, and a lift
that costs faithfulness is a bad trade.

    # Claude (the current writer)
    python -m evals.harness.run --suite writer --writer claude --limit 60
    # base Mistral and the tuned adapter, served by one vLLM process
    python -m evals.harness.run --suite writer --writer mistral --mistral-model mistralai/Mistral-7B-Instruct-v0.3
    python -m evals.harness.run --suite writer --writer mistral --mistral-model papermind
    python -m evals.harness.report --compare-writers   # side by side, newest run of each writer

"Calibration" here means expertise-level calibration, not confidence calibration.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from evals.harness import scorers
from evals.harness.dataset import BenchmarkRow, PaperLibrary
from evals.harness.judges import Judges, SourceText
from papermind.agent.labels import label_sources
from papermind.agent.writer import Writer, WriterInput
from papermind.schemas.tools import RetrievedChunk

PLAN = "Answer the question from the source, at the reader's level."


@dataclass
class WriterRow:
    id: str
    paper_id: str
    level: str
    question: str
    writer: str
    answer: str = ""
    judged_level: str | None = None
    faithfulness: float | None = None
    # Leaving out claims the answer itself marks as general background (not from the paper).
    faithfulness_paper_claims: float | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    judge_cost_usd: float = 0.0
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.answer)


async def write_one(
    writer: Writer, row: BenchmarkRow, library: PaperLibrary, judges: Judges | None
) -> WriterRow:
    out = WriterRow(
        id=row.id, paper_id=row.paper_id, level=row.target_level, question=row.question, writer=writer.name
    )
    record = library.record(row.paper_id)
    chunk = library.chunks(row.paper_id).get(row.gold_chunk)
    if record is None or chunk is None:
        out.error = "gold chunk not found"
        return out
    retrieved = RetrievedChunk(
        chunk_id=chunk.chunk_id,
        section_id=chunk.section_id,
        section_number=chunk.section_number,
        section_title=chunk.section_title,
        text=chunk.text,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        rank=1,
        score=1.0,
    )
    inp = WriterInput(
        question=row.question,
        level=row.target_level,
        paper=record.meta,
        plan=PLAN,
        altitude="technical" if row.target_level == "expert" else "scaffolded",
        sources=label_sources([retrieved]),
    )
    t0 = time.perf_counter()
    first: float | None = None
    parts: list[str] = []
    try:
        async for piece in writer.stream(inp):
            first = first or time.perf_counter()
            parts.append(piece)
    except Exception as exc:  # one failed answer is a data point, not a crashed run
        out.error = f"{type(exc).__name__}: {getattr(exc, 'message', None) or exc}"
        return out
    out.answer = "".join(parts)
    out.latency_ms = int((time.perf_counter() - t0) * 1000)
    out.ttft_ms = int((first - t0) * 1000) if first else None
    if judges is not None and out.answer.strip():
        verdict = await judges.judge_answer(
            out.answer,
            [SourceText(label="S1", chunk_id=chunk.chunk_id, text=chunk.text, section=chunk.section_title)],
            paper_title=record.meta.title,
            which=("calibration", "faithfulness"),
        )
        out.judged_level = verdict.calibration.level if verdict.calibration else None
        out.faithfulness = verdict.faithfulness.score if verdict.faithfulness else None
        out.faithfulness_paper_claims = verdict.faithfulness.score_unmarked if verdict.faithfulness else None
        out.judge_cost_usd = verdict.cost_usd
    return out


async def run_writer(
    rows: list[BenchmarkRow], writer: Writer, judges: Judges | None, *, concurrency: int = 4
) -> list[WriterRow]:
    library = PaperLibrary()
    gate = asyncio.Semaphore(concurrency)

    async def guarded(row: BenchmarkRow) -> WriterRow:
        async with gate:
            return await write_one(writer, row, library, judges)

    return list(await asyncio.gather(*(guarded(r) for r in rows)))


def summarize_writer(results: list[WriterRow]) -> dict[str, Any]:
    ok = [r for r in results if r.ok]
    labels = [r.judged_level for r in ok]
    targets = [r.level for r in ok]
    return {
        "writer": results[0].writer if results else None,
        "n": len(results),
        "errors": sum(not r.ok for r in results),
        "calibration": scorers.calibration_accuracy(labels, targets),
        "calibration_by_level": scorers.per_level_accuracy(labels, targets),
        "confusion": scorers.confusion_matrix(labels, targets),
        "faithfulness": scorers.mean(r.faithfulness for r in ok),
        "faithfulness_paper_claims": scorers.mean(r.faithfulness_paper_claims for r in ok),
        "latency": scorers.latency_summary(r.latency_ms for r in ok),
        "ttft": scorers.latency_summary(r.ttft_ms for r in ok),
        "judge_cost_usd": round(sum(r.judge_cost_usd for r in results), 4),
    }


def writer_table(summary: dict[str, Any]) -> str:
    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v * 100:.1f}%"

    by_level = summary.get("calibration_by_level") or {}
    lines = [
        f"writer: {summary['writer']}   rows: {summary['n']}   errors: {summary['errors']}",
        f"calibration (judged level == requested): {pct(summary['calibration'])}",
        "  "
        + "   ".join(
            f"{lvl}: {pct((by_level.get(lvl) or {}).get('accuracy'))}"
            for lvl in ("beginner", "intermediate", "expert")
        ),
        f"faithfulness: {pct(summary['faithfulness'])} of all claims, "
        f"{pct(summary['faithfulness_paper_claims'])} of claims presented as the paper's",
        f"latency p50: {summary['latency'].get('p50')} ms   first token p50: {summary['ttft'].get('p50')} ms",
    ]
    return "\n".join(lines)


def to_dicts(results: list[WriterRow]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
