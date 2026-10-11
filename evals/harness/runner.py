"""Run benchmark rows through the real agent, headlessly: no HTTP, no Redis.

One ``AgentHarness`` per run starts the three MCP servers once, builds the
same ``AgentRunner`` the API uses, and answers each row in a fresh in-memory
session. The emit callback only collects; what the evals need comes from the
returned ``TurnResult`` and its ``Trace`` (exact retrieved chunk ids per
search, every model call's usage and cache tokens, tool calls, errors).

Recall inside an agent turn, three ways, all reported:

* ``recall_first5`` -- the gold chunk is in the first search_paper call's top 5.
  Measures the agent's first query rewrite plus the retriever.
* ``recall_union`` -- the gold chunk came back from any search this turn.
  An upper bound: retrieved is not the same as shown to the writer.
* ``recall_writer`` -- the gold chunk is among the passages handed to the
  writer (``TurnResult.sources``; the agent's ``compose_answer`` choice, or
  the top 5 retrieved when it made none). This is the headline: the top 5
  the writer actually saw is what the answer could rest on.

The eval readers. Prerequisite gaps are computed per reader from KNOWS edges,
and a reader with no edges is missing everything -- every answer would be
pitched at intuition altitude and calibration would measure the harness, not
the writer. So each level is answered for a fixed synthetic reader whose
knowledge matches the level's definition, seeded through the prereq_graph
server's own ``mark_known`` before the run (so it lands in whichever store
that server uses, Neo4j or in-memory). The mastery pass is off for the same
reason: it would rewrite these readers' knowledge mid-run and make each answer
depend on which questions ran before it. It also costs a small classifier
call per turn that the reported cost therefore excludes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from evals.harness import scorers
from evals.harness.dataset import BenchmarkRow, PaperLibrary, answer_text_hash
from evals.harness.judges import AnswerJudgement, Judges, SourceText
from papermind.agent.loop import AgentRunner, TurnContext, TurnResult
from papermind.agent.registry import build_registry
from papermind.agent.writer import make_writer
from papermind.config import Settings
from papermind.graph.seed import default_areas_path, load_catalog
from papermind.llm.client import LLM, LLMClient
from papermind.mcp_client.manager import MCPCaller, MCPManager, ToolExecutionError
from papermind.memory.users import ONBOARDING_CONFIDENCE, onboarding_concepts
from papermind.schemas.events import ChatEvent
from papermind.schemas.session import Session, UserProfile
from papermind.schemas.tools import Level

log = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = 4
RETRY_DELAY_S = 5.0
SEED_CONCURRENCY = 8

_FOUNDATIONS = (
    "linear-algebra",
    "calculus-optimization",
    "probability-statistics",
    "machine-learning-basics",
)
# Matched to LEVEL_DEFINITIONS: intermediate "assumes linear algebra and basic
# machine learning"; expert "assumes familiarity with transformers". A
# beginner is assumed to know nothing. The version in the id changes whenever
# these lists do: KNOWS edges are only ever added here, so a reader whose
# areas shrank must be a new reader.
EVAL_READERS: dict[Level, tuple[str, tuple[str, ...]]] = {
    "beginner": ("u_eval_v1_beginner", ()),
    "intermediate": ("u_eval_v1_intermediate", (*_FOUNDATIONS, "neural-networks")),
    "expert": (
        "u_eval_v1_expert",
        (
            *_FOUNDATIONS,
            "neural-networks",
            "vision-models",
            "sequence-models-nlp",
            "attention-transformers",
            "large-language-models",
            "evaluation-datasets",
        ),
    ),
}


@dataclass
class ReaderProfile:
    level: Level
    user_id: str
    known: list[str]  # concept names, for TurnContext.known_summary


async def seed_readers(mcp: MCPCaller, settings: Settings) -> dict[Level, ReaderProfile]:
    """Write each eval reader's onboarding knowledge through the prereq_graph server. Idempotent."""
    catalog = await asyncio.to_thread(
        load_catalog, settings.concepts_path, default_areas_path(settings.concepts_path)
    )
    gate = asyncio.Semaphore(SEED_CONCURRENCY)
    readers: dict[Level, ReaderProfile] = {}
    failures = 0

    async def mark(user_id: str, concept_id: str) -> None:
        nonlocal failures
        async with gate:
            try:
                await mcp.call(
                    "prereq_graph",
                    "mark_known",
                    {
                        "user_id": user_id,
                        "concept": concept_id,
                        "confidence": ONBOARDING_CONFIDENCE,
                        "source": "onboarding",
                        "evidence": "Eval reader profile (evals/harness/runner.py).",
                    },
                )
            except ToolExecutionError as exc:
                failures += 1
                if failures == 1:
                    log.warning("seeding eval readers failed: %s", exc.message)

    for level, (user_id, areas) in EVAL_READERS.items():
        concept_ids = onboarding_concepts(catalog, areas)
        await asyncio.gather(*(mark(user_id, cid) for cid in concept_ids))
        names = sorted((catalog.concepts[cid].name for cid in concept_ids), key=str.casefold)
        readers[level] = ReaderProfile(level=level, user_id=user_id, known=names)
    if failures:
        log.warning("%d mark_known calls failed; prerequisite gaps for eval readers may be wrong", failures)
    return readers


class _Collector:
    """The emit callback: keeps event timings, nothing else (the TurnResult has the content)."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.answer_done_ms: int | None = None
        self.types: list[str] = []

    async def __call__(self, event: ChatEvent) -> None:
        self.types.append(event.type)
        if event.type == "answer.done":
            self.answer_done_ms = round((time.perf_counter() - self.started) * 1000)


@dataclass
class AgentRowResult:
    """One benchmark row answered by the agent: everything the scorers and the report read."""

    id: str
    paper_id: str
    level: str
    origin: str
    query_type: str
    question: str
    gold_chunk: str
    reference_answer: str
    answer: str = ""
    answer_hash: str = ""
    cited: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    searches: list[list[str]] = field(default_factory=list)
    first_search_top5: list[str] = field(default_factory=list)
    union: list[str] = field(default_factory=list)
    writer_chunks: list[str] = field(default_factory=list)
    recall_first5: float | None = None
    recall_union: float | None = None
    recall_writer: float | None = None
    altitude: str | None = None
    plan: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None
    ttft_ms: int | None = None
    answer_ms: int | None = None
    model_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    trace_notes: list[str] = field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    attempts: int = 1
    f1: float | None = None
    judgement: dict[str, Any] | None = None
    faithfulness: float | None = None
    claims_supported: int = 0
    claims_total: int = 0
    calibration_label: str | None = None
    citation_precision: float | None = None
    citations_supported: int = 0
    citations_total: int = 0
    paragraphs_paper_derived: int = 0
    paragraphs_uncited: int = 0
    judge_cost_usd: float = 0.0
    judge_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Errored turns are reported as errors and left out of quality means, never scored as zero."""
        return self.error is None and bool(self.answer.strip())

    @property
    def calibration_correct(self) -> bool | None:
        return None if self.calibration_label is None else self.calibration_label == self.level

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["ok"] = self.ok
        out["calibration_correct"] = self.calibration_correct
        return out

    def apply_judgement(self, judgement: AnswerJudgement) -> None:
        self.judgement = judgement.to_dict()
        self.judge_cost_usd = round(judgement.cost_usd, 6)
        self.judge_errors = list(judgement.errors)
        if judgement.faithfulness is not None:
            self.faithfulness = judgement.faithfulness.score
            self.claims_supported = judgement.faithfulness.supported
            self.claims_total = judgement.faithfulness.total
        if judgement.calibration is not None:
            self.calibration_label = judgement.calibration.level
        if judgement.citations is not None:
            self.citation_precision = judgement.citations.precision
            self.citations_total = len(judgement.citations.items)
            self.citations_supported = sum(i.supported for i in judgement.citations.items)
        if judgement.paragraphs is not None:
            self.paragraphs_paper_derived = judgement.paragraphs.paper_derived
            self.paragraphs_uncited = judgement.paragraphs.uncited


def score_retrieval(out: AgentRowResult, result: TurnResult) -> None:
    out.searches = [list(ids) for ids in result.retrieved]
    out.first_search_top5 = out.searches[0][:5] if out.searches else []
    out.union = list(dict.fromkeys(cid for ids in out.searches for cid in ids))
    out.writer_chunks = [s.chunk_id for s in result.sources]
    # A turn that never searched retrieved nothing: a miss, not "n/a".
    out.recall_first5 = scorers.recall_at_k(out.gold_chunk, out.first_search_top5, 5)
    out.recall_union = 1.0 if out.gold_chunk in out.union else 0.0
    out.recall_writer = 1.0 if out.gold_chunk in out.writer_chunks else 0.0


def _record_turn(out: AgentRowResult, result: TurnResult, collector: _Collector) -> None:
    out.answer = result.answer
    out.answer_hash = answer_text_hash(result.answer)
    out.cited = list(result.cited)
    out.sources = [
        {"label": s.label, "chunk_id": s.chunk_id, "section": s.section_number or s.section_title}
        for s in result.sources
    ]
    out.altitude = result.altitude
    out.plan = result.plan
    out.usage = result.usage.model_dump(exclude={"type"})
    out.latency_ms = result.latency_ms
    out.ttft_ms = result.ttft_ms
    out.answer_ms = collector.answer_done_ms
    out.model_calls = [
        {
            "purpose": c.purpose,
            "step": c.step,
            "latency_ms": c.latency_ms,
            "ttft_ms": c.ttft_ms,
            "input_tokens": c.input_tokens,
            "cache_read_input_tokens": c.cache_read_input_tokens,
            "cache_creation_input_tokens": c.cache_creation_input_tokens,
            "output_tokens": c.output_tokens,
            "cost_usd": c.cost_usd,
            "request_id": c.request_id,
        }
        for c in result.trace.model_calls
    ]
    out.tool_calls = [
        {"step": t.step, "tool": t.tool, "ok": t.ok, "duration_ms": t.duration_ms, "error_kind": t.error_kind}
        for t in result.trace.tool_calls
    ]
    out.trace_notes = list(result.trace.errors)
    out.error = result.error.message if result.error else None
    out.error_code = result.error.code if result.error else None
    score_retrieval(out, result)
    out.f1 = scorers.token_f1(result.answer, out.reference_answer) if out.ok else None


class AgentHarness:
    """The agent, its MCP servers and the judges, started once for a whole run."""

    def __init__(
        self,
        settings: Settings,
        *,
        library: PaperLibrary | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        judge: bool = True,
        llm: LLMClient | None = None,
        mcp: MCPCaller | None = None,
    ) -> None:
        """``llm`` / ``mcp`` replace the real client and servers (offline tests); the caller then owns them."""
        self.settings = settings
        self.library = library or PaperLibrary(settings.papers_dir)
        self.concurrency = max(1, concurrency)
        self.judge = judge
        self._llm: LLMClient | None = llm
        self._mcp: MCPCaller | None = mcp
        self._owned: list[LLM | MCPManager] = []
        self._runner: AgentRunner | None = None
        self._judges: Judges | None = None
        self.readers: dict[Level, ReaderProfile] = {}

    async def __aenter__(self) -> AgentHarness:
        try:
            await self.start()
        except BaseException:
            # A half-started harness still has server processes to stop.
            await self.aclose()
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def start(self) -> None:
        if self._llm is None:
            llm = LLM(self.settings)  # raises PaperMindLLMError("no_api_key") before anything spawns
            self._owned.append(llm)
            self._llm = llm
        if self._mcp is None:
            manager = MCPManager(self.settings)
            self._owned.append(manager)
            # Without the retriever every answer is ungrounded and every metric
            # is noise; the other two servers degrade the way they do in the app.
            await manager.start(required=("retriever",))
            self._mcp = manager
        registry = build_registry()
        registry.verify_against(self._mcp)
        self._runner = AgentRunner(
            llm=self._llm,
            mcp=self._mcp,
            registry=registry,
            writer=make_writer(self.settings, self._llm),
            settings=self.settings,
            mastery=None,
        )
        self.readers = await seed_readers(self._mcp, self.settings)
        if self.judge:
            self._judges = Judges(self._llm, self.settings)

    async def aclose(self) -> None:
        # Servers first: they may still be answering a call the client is waiting on.
        for resource in reversed(self._owned):
            await resource.aclose()
        self._owned.clear()

    def _context(self, row: BenchmarkRow) -> TurnContext:
        record = self.library.record(row.paper_id)
        if record is None:
            raise LookupError(f"paper {row.paper_id} is not ingested")
        reader = self.readers.get(row.target_level) or ReaderProfile(
            row.target_level, EVAL_READERS[row.target_level][0], []
        )
        now = datetime.now(UTC)
        session = Session(
            session_id=f"s_eval_{row.id}",
            user_id=reader.user_id,
            paper_id=row.paper_id,
            level=row.target_level,
            created_at=now,
            updated_at=now,
        )
        return TurnContext(
            session=session,
            user=UserProfile(user_id=reader.user_id, level=row.target_level, onboarded=True, created_at=now),
            paper=record.meta,
            question=row.question,
            quote=None,
            level=row.target_level,
            known_summary=reader.known,
        )

    def sources_for(self, row: BenchmarkRow, out: AgentRowResult) -> list[SourceText]:
        """The passages the writer was given, with their full text from paper.json."""
        chunks = self.library.chunks(row.paper_id)
        found: list[SourceText] = []
        for s in out.sources:
            chunk = chunks.get(s["chunk_id"])
            if chunk is not None:
                found.append(SourceText(s["label"], chunk.chunk_id, chunk.text, s.get("section") or ""))
        return found

    async def run_row(self, row: BenchmarkRow) -> AgentRowResult:
        """Answer one row (one retry on a retryable API error), then judge it if judging is on."""
        assert self._runner is not None, "start() the harness first"
        out = AgentRowResult(
            id=row.id,
            paper_id=row.paper_id,
            level=row.target_level,
            origin=row.origin,
            query_type=row.query_type,
            question=row.question,
            gold_chunk=row.gold_chunk,
            reference_answer=row.reference_answer,
        )
        billed = 0.0
        for attempt in (1, 2):
            out.attempts = attempt
            collector = _Collector()
            try:
                result = await self._runner.run_turn(self._context(row), collector)
            except Exception as exc:  # run_turn catches its own; this is the harness failing
                log.exception("row %s crashed the harness", row.id)
                out.error, out.error_code = repr(exc), "internal"
                return out
            _record_turn(out, result, collector)
            # A failed attempt was billed too; latency is the final attempt's alone.
            out.usage["cost_usd"] = round(out.usage.get("cost_usd", 0.0) + billed, 6)
            billed = out.usage["cost_usd"]
            if result.error is None or not result.error.retryable or attempt == 2:
                break
            log.warning("row %s: %s, retrying in %.0fs", row.id, result.error.code, RETRY_DELAY_S)
            await asyncio.sleep(RETRY_DELAY_S)

        if self._judges is not None and out.ok:
            record = self.library.record(row.paper_id)
            judgement = await self._judges.judge_answer(
                out.answer, self.sources_for(row, out), paper_title=record.meta.title if record else ""
            )
            out.apply_judgement(judgement)
        return out

    async def run_rows(
        self,
        rows: Sequence[BenchmarkRow],
        *,
        on_done: Callable[[AgentRowResult], None] | None = None,
    ) -> list[AgentRowResult]:
        """All rows, at most ``concurrency`` at a time, returned in input order."""
        gate = asyncio.Semaphore(self.concurrency)

        async def one(row: BenchmarkRow) -> AgentRowResult:
            async with gate:
                result = await self.run_row(row)
            if on_done is not None:
                on_done(result)
            return result

        return list(await asyncio.gather(*(one(r) for r in rows)))


def latency_settings(settings: Settings, config: str) -> Settings:
    """The two arms of the latency A/B. Only the two switches differ; prewarming is off in both."""
    switches = {
        "baseline": {"parallel_tools": False, "prompt_caching": False},
        "optimized": {"parallel_tools": True, "prompt_caching": True},
    }
    if config not in switches:
        raise ValueError(f"unknown latency config {config!r}; expected one of {sorted(switches)}")
    return settings.model_copy(update={**switches[config], "prewarm_cache": False})
