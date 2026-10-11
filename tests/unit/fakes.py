"""Fakes for the agent's collaborators: the model (``FakeLLM``) and the MCP servers (``FakeMCP``).

Both implement the same protocols the real classes do (``papermind.llm.client.LLMClient``
and ``papermind.mcp_client.manager.MCPCaller``), so the agent loop, the API and
the evals can run a whole turn with no network, no API key and no services.

``FakeLLM`` is scripted. Each model call pops the next entry from the script
for its purpose -- the agent (a stream call with tools), the writer (a stream
call without tools) or the mastery classifier (``parse``) -- and every request
is recorded, deep-copied at call time, for assertions on what was sent::

    llm = FakeLLM(
        agent=[
            agent_turn(tool_use("search_paper", {"query": "scaled dot-product attention"})),
            agent_turn(compose("Explain the scaling.", ["p:sec_3.2.1"])),
        ],
        writer=["Attention divides by $\\sqrt{d_k}$ [S1]."],
    )

``FakeMCP`` serves the routed tools from small in-process handlers: search over
a fixed list of chunks, the real prerequisite-graph service over the sample
catalog in ``papermind.graph.testing``, and canned Semantic Scholar papers. Any
tool can be given a delay or a failure, and every call is timed, which is how
the tests prove that parallel dispatch really overlaps.
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from papermind.config import Settings
from papermind.graph import service as graph_service
from papermind.graph.store import InMemoryGraphStore
from papermind.graph.testing import sample_catalog
from papermind.llm.client import CallResult, PaperMindLLMError, llm_error
from papermind.llm.pricing import Usage, cost_of
from papermind.mcp_client.manager import ToolExecutionError
from papermind.schemas.events import ChatEvent
from papermind.schemas.paper import PaperMeta, Section
from papermind.schemas.session import KnownConcept, Quote, Session, UserProfile
from papermind.schemas.tools import (
    CheckPrerequisitesServerArgs,
    ForgetConceptServerArgs,
    LearningPathServerArgs,
    LookupPaperArgs,
    LookupPaperResult,
    MarkKnownServerArgs,
    PaperGraphResult,
    PaperGraphServerArgs,
    RetrievedChunk,
    ScholarPaper,
    SearchPaperResult,
    SearchPaperServerArgs,
)

MODEL = "claude-opus-5-5"
PAPER_ID = "1706.03762"
USER_ID = "u_test"
_ids = itertools.count(1)


def fake_settings(**overrides: Any) -> Settings:
    """Settings that ignore the repo's ``.env`` (no API key leaks into unit tests)."""
    return Settings(_env_file=None, **{"anthropic_api_key": None, **overrides})


# ---------------------------------------------------------------- content blocks


def tool_use(name: str, input: Any, *, id: str | None = None) -> dict[str, Any]:
    return {"type": "tool_use", "id": id or f"toolu_{next(_ids):04d}", "name": name, "input": input}


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def thinking(value: str = "", signature: str = "sig-abc") -> dict[str, Any]:
    return {"type": "thinking", "thinking": value, "signature": signature}


def compose(
    plan: str = "Explain the mechanism from the passages, at the reader's level.",
    source_chunk_ids: Sequence[str] = (),
    *,
    concepts_to_scaffold: Sequence[str] = (),
    external_papers: Sequence[str] = (),
    id: str | None = None,
) -> dict[str, Any]:
    return tool_use(
        "compose_answer",
        {
            "plan": plan,
            "source_chunk_ids": list(source_chunk_ids),
            "concepts_to_scaffold": list(concepts_to_scaffold),
            "external_papers": list(external_papers),
        },
        id=id,
    )


@dataclass
class Scripted:
    """One scripted model response."""

    content: list[dict[str, Any]]
    stop_reason: str | None = None
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=200, output_tokens=40))
    delay_s: float = 0.0
    parsed: BaseModel | None = None
    model: str = MODEL


def agent_turn(*blocks: dict[str, Any], stop_reason: str | None = None, **kwargs: Any) -> Scripted:
    """An agent response; ``stop_reason`` defaults to tool_use when it calls a tool."""
    if stop_reason is None:
        stop_reason = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return Scripted(content=list(blocks), stop_reason=stop_reason, **kwargs)


ScriptEntry = Scripted | str | BaseModel | BaseException | Callable[[dict[str, Any]], Any]


# ---------------------------------------------------------------- the model


@dataclass
class RecordedCall:
    purpose: str  # "agent" | "writer" | "mastery" | "prewarm"
    kwargs: dict[str, Any]
    started: float
    finished: float = 0.0


class FakeLLM:
    """A scripted ``LLMClient``. Raises AssertionError when a script runs dry."""

    def __init__(
        self,
        *,
        agent: Iterable[ScriptEntry] = (),
        writer: Iterable[ScriptEntry] = (),
        mastery: Iterable[ScriptEntry] = (),
        model: str = MODEL,
        writer_chunk_chars: int = 12,
    ) -> None:
        self.model = model
        self._scripts: dict[str, list[ScriptEntry]] = {
            "agent": list(agent),
            "writer": list(writer),
            "mastery": list(mastery),
        }
        self._chunk = writer_chunk_chars
        self.calls: list[RecordedCall] = []

    # The recorded requests, by purpose.
    def requests(self, purpose: str) -> list[dict[str, Any]]:
        return [c.kwargs for c in self.calls if c.purpose == purpose]

    def remaining(self, purpose: str) -> int:
        return len(self._scripts[purpose])

    async def stream(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        effort: str,
        max_tokens: int,
        cache: bool,
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> CallResult:
        purpose = "agent" if tools else "writer"
        kwargs = {
            "system": system,
            "messages": messages,
            "tools": tools,
            "effort": effort,
            "max_tokens": max_tokens,
            "cache": cache,
        }
        call = self._record(purpose, kwargs)
        entry = self._next(purpose, kwargs)
        scripted = self._as_scripted(entry, purpose)
        if scripted.delay_s:
            await asyncio.sleep(scripted.delay_s)
        if on_text is not None:
            for block in scripted.content:
                if block["type"] == "text":
                    for i in range(0, len(block["text"]), self._chunk):
                        await on_text(block["text"][i : i + self._chunk])
        call.finished = time.perf_counter()
        result = self._result(scripted, call)
        if result.stop_reason == "refusal":
            raise llm_error("refusal", result=result)
        return result

    async def parse(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        effort: str,
        max_tokens: int,
    ) -> CallResult:
        kwargs = {
            "system": system,
            "messages": messages,
            "schema": schema,
            "effort": effort,
            "max_tokens": max_tokens,
        }
        call = self._record("mastery", kwargs)
        entry = self._next("mastery", kwargs)
        scripted = self._as_scripted(entry, "mastery")
        if scripted.parsed is not None and not isinstance(scripted.parsed, schema):
            scripted.parsed = schema.model_validate(scripted.parsed.model_dump())
        if scripted.delay_s:
            await asyncio.sleep(scripted.delay_s)
        call.finished = time.perf_counter()
        return self._result(scripted, call)

    async def prewarm(
        self, *, system: list[dict[str, Any]], tools: list[dict[str, Any]] | None, effort: str
    ) -> CallResult:
        call = self._record("prewarm", {"system": system, "tools": tools, "effort": effort})
        call.finished = time.perf_counter()
        return self._result(Scripted(content=[], stop_reason="max_tokens", usage=Usage()), call)

    # ------------------------------------------------------------ internals

    def _record(self, purpose: str, kwargs: dict[str, Any]) -> RecordedCall:
        # Deep-copied now: the loop appends to the same messages list afterwards.
        snapshot = {k: (v if k == "schema" else copy.deepcopy(v)) for k, v in kwargs.items()}
        call = RecordedCall(purpose=purpose, kwargs=snapshot, started=time.perf_counter())
        self.calls.append(call)
        return call

    def _next(self, purpose: str, kwargs: dict[str, Any]) -> ScriptEntry:
        script = self._scripts[purpose]
        if not script:
            raise AssertionError(f"FakeLLM: no scripted {purpose} response left for this call")
        entry = script.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        if callable(entry) and not isinstance(entry, BaseModel | Scripted):
            entry = entry(kwargs)
            if isinstance(entry, BaseException):
                raise entry
        return entry

    @staticmethod
    def _as_scripted(entry: Any, purpose: str) -> Scripted:
        if isinstance(entry, Scripted):
            return entry
        if isinstance(entry, str):
            return Scripted(content=[text(entry)], stop_reason="end_turn")
        if isinstance(entry, BaseModel):
            return Scripted(content=[text(entry.model_dump_json())], stop_reason="end_turn", parsed=entry)
        raise TypeError(f"FakeLLM: cannot use {entry!r} as a {purpose} response")

    def _result(self, scripted: Scripted, call: RecordedCall) -> CallResult:
        return CallResult(
            message=None,
            usage=scripted.usage,
            latency_ms=round((call.finished - call.started) * 1000),
            ttft_ms=0,
            request_id=f"req_fake_{len(self.calls)}",
            cost_usd=cost_of(scripted.usage, scripted.model),
            content=copy.deepcopy(scripted.content),
            stop_reason=scripted.stop_reason,
            model=scripted.model,
            parsed=scripted.parsed,
        )


def refusal() -> PaperMindLLMError:
    return llm_error("refusal", result=None)


# ---------------------------------------------------------------- the MCP servers


def make_chunk(
    slug: str,
    text: str,
    *,
    number: str | None = "3.2",
    title: str = "Attention",
    rank: int = 1,
    page: int = 3,
    paper_id: str = PAPER_ID,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=f"{paper_id}:sec_{number or 'x'}_{slug}",
        section_id=f"sec_{number or 'x'}",
        section_number=number,
        section_title=title,
        text=text,
        page_start=page,
        page_end=page,
        rank=rank,
        score=1.0 / rank,
    )


DEFAULT_CHUNKS: tuple[RetrievedChunk, ...] = (
    make_chunk(
        "scaled_dot_product_attention",
        "We call our particular attention Scaled Dot-Product Attention. We compute the dot products of the "
        "query with all keys, divide each by sqrt(d_k), and apply a softmax function to obtain the weights.",
        number="3.2.1",
        title="Scaled Dot-Product Attention",
    ),
    make_chunk(
        "multi_head_attention",
        "Multi-head attention allows the model to jointly attend to information from different "
        "representation subspaces at different positions.",
        number="3.2.2",
        title="Multi-Head Attention",
        page=4,
    ),
    make_chunk(
        "training",
        "We trained on the standard WMT 2014 English-German dataset consisting of about 4.5 million "
        "sentence pairs. We used label smoothing of value 0.1 and evaluated with BLEU.",
        number="5",
        title="Training",
        page=7,
    ),
)

BLEU_PAPER = ScholarPaper(
    s2_id="d7da009f457917aa381619facfa5ffae9329a6e9",
    title="Bleu: a Method for Automatic Evaluation of Machine Translation",
    year=2002,
    authors=["Kishore Papineni", "Salim Roukos", "T. Ward", "Wei-Jing Zhu"],
    abstract="Human evaluations of machine translation are extensive but expensive...",
    citation_count=30000,
)

Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

SERVER_ARGS: dict[tuple[str, str], type[BaseModel]] = {
    ("retriever", "search_paper"): SearchPaperServerArgs,
    ("prereq_graph", "check_prerequisites"): CheckPrerequisitesServerArgs,
    ("prereq_graph", "get_learning_path"): LearningPathServerArgs,
    ("prereq_graph", "mark_known"): MarkKnownServerArgs,
    ("prereq_graph", "forget_concept"): ForgetConceptServerArgs,
    ("semantic_scholar", "lookup_paper"): LookupPaperArgs,
    ("semantic_scholar", "get_references"): PaperGraphServerArgs,
    ("semantic_scholar", "get_citations"): PaperGraphServerArgs,
}


@dataclass
class MCPCall:
    server: str
    tool: str
    args: dict[str, Any]
    started: float
    finished: float = 0.0


class FakeMCP:
    """An in-process ``MCPCaller`` with the three servers' tools.

    ``delays[tool]`` sleeps before answering; ``failures[tool]`` is raised
    instead of answering (a ``ToolExecutionError``, or any exception); a
    custom ``handlers[(server, tool)]`` replaces the default behaviour.
    Arguments are validated with the real ``*ServerArgs`` models, as the real
    servers do, so an injection bug fails here too.
    """

    def __init__(
        self,
        *,
        chunks: Sequence[RetrievedChunk] = DEFAULT_CHUNKS,
        papers: Sequence[ScholarPaper] = (BLEU_PAPER,),
        graph: InMemoryGraphStore | None = None,
        delays: dict[str, float] | None = None,
        failures: dict[str, BaseException] | None = None,
        handlers: dict[tuple[str, str], Handler] | None = None,
        known_threshold: float = 0.6,
    ) -> None:
        self.chunks = list(chunks)
        self.papers = list(papers)
        self.graph = graph or InMemoryGraphStore(sample_catalog())
        self.delays = delays or {}
        self.failures = failures or {}
        self.known_threshold = known_threshold
        self.calls: list[MCPCall] = []
        self._handlers: dict[tuple[str, str], Handler] = {
            ("retriever", "search_paper"): self._search,
            ("prereq_graph", "check_prerequisites"): self._check,
            ("prereq_graph", "get_learning_path"): self._path,
            ("prereq_graph", "mark_known"): self._mark,
            ("prereq_graph", "forget_concept"): self._forget,
            ("semantic_scholar", "lookup_paper"): self._lookup,
            ("semantic_scholar", "get_references"): self._references,
            ("semantic_scholar", "get_citations"): self._references,
            **(handlers or {}),
        }

    # ------------------------------------------------------------ MCPCaller

    async def call(self, server: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        record = MCPCall(server=server, tool=tool, args=copy.deepcopy(args), started=time.perf_counter())
        self.calls.append(record)
        try:
            if (delay := self.delays.get(tool)) is not None:
                await asyncio.sleep(delay)
            if (failure := self.failures.get(tool)) is not None:
                raise failure
            handler = self._handlers.get((server, tool))
            if handler is None:
                raise ToolExecutionError(server, tool, f"unknown tool {tool!r}", kind="tool_error")
            model = SERVER_ARGS.get((server, tool))
            if model is not None:
                model.model_validate(args)
            return await handler(args)
        finally:
            record.finished = time.perf_counter()

    def tool_names(self, server: str) -> set[str]:
        return {tool for (srv, tool) in self._handlers if srv == server}

    def tool_schemas(self, server: str) -> dict[str, dict[str, Any]]:
        return {
            tool: model.model_json_schema()
            for (srv, tool), model in SERVER_ARGS.items()
            if srv == server and (srv, tool) in self._handlers
        }

    def calls_to(self, tool: str) -> list[MCPCall]:
        return [c for c in self.calls if c.tool == tool]

    # ------------------------------------------------------------ default tools

    async def _search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args["query"]
        words = {w.lower() for w in query.split()}
        k = args.get("k", 5)
        section = args.get("section_filter")
        pool = [c for c in self.chunks if not section or (c.section_number or "").startswith(section)]
        scored = sorted(
            pool, key=lambda c: -sum(w in c.text.lower() or w in c.section_title.lower() for w in words)
        )
        ranked = [c.model_copy(update={"rank": i}) for i, c in enumerate(scored[:k], start=1)]
        return SearchPaperResult(
            paper_id=args["paper_id"], query=query, mode=args.get("mode", "hybrid"), chunks=ranked
        ).model_dump(mode="json")

    async def _check(self, args: dict[str, Any]) -> dict[str, Any]:
        result = await graph_service.check_prerequisites(
            self.graph, args["user_id"], args["concept"], known_threshold=self.known_threshold
        )
        return result.model_dump(mode="json")

    async def _path(self, args: dict[str, Any]) -> dict[str, Any]:
        result = await graph_service.learning_path(
            self.graph, args["user_id"], args["concept"], known_threshold=self.known_threshold
        )
        return result.model_dump(mode="json")

    async def _mark(self, args: dict[str, Any]) -> dict[str, Any]:
        return (await self.graph.mark(MarkKnownServerArgs.model_validate(args))).model_dump(mode="json")

    async def _forget(self, args: dict[str, Any]) -> dict[str, Any]:
        return (await self.graph.forget(args["user_id"], args["concept"])).model_dump(mode="json")

    async def _lookup(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args["query"].lower()
        matches = [p for p in self.papers if any(w in p.title.lower() for w in query.split())]
        return LookupPaperResult(query=args["query"], matches=matches[: args.get("limit", 3)]).model_dump(
            mode="json"
        )

    async def _references(self, args: dict[str, Any]) -> dict[str, Any]:
        paper = args.get("paper") or args.get("default_paper") or ""
        return PaperGraphResult(paper=paper, papers=self.papers[: args.get("limit", 10)]).model_dump(
            mode="json"
        )


# ---------------------------------------------------------------- sessions and turns


def make_paper(**overrides: Any) -> PaperMeta:
    fields: dict[str, Any] = {
        "paper_id": PAPER_ID,
        "source": "arxiv",
        "arxiv_id": PAPER_ID,
        "arxiv_version": "v7",
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
        "abstract": "The dominant sequence transduction models are based on complex recurrent networks...",
        "num_pages": 15,
        "sections": [
            Section(section_id="sec_1", number="1", title="Introduction", page=1),
            Section(section_id="sec_3", number="3", title="Model Architecture", page=2),
            Section(section_id="sec_3.2", number="3.2", title="Attention", level=2, page=3),
            Section(
                section_id="sec_3.2.1", number="3.2.1", title="Scaled Dot-Product Attention", level=3, page=3
            ),
            Section(section_id="sec_5", number="5", title="Training", page=7),
        ],
        "chunk_count": 40,
        "chunker_version": "test",
        "ingested_at": datetime(2026, 10, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return PaperMeta(**fields)


def make_user(user_id: str = USER_ID, level: str = "beginner") -> UserProfile:
    return UserProfile(user_id=user_id, level=level, created_at=datetime(2026, 10, 1, tzinfo=UTC))


def make_session(*, level: str = "beginner", messages: Sequence[Any] = (), user_id: str = USER_ID) -> Session:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    return Session(
        session_id="s_test",
        user_id=user_id,
        paper_id=PAPER_ID,
        level=level,
        messages=list(messages),
        created_at=now,
        updated_at=now,
    )


def make_ctx(
    question: str = "How does attention work?",
    *,
    level: str = "beginner",
    quote: Quote | None = None,
    known: Sequence[str] = (),
    knowledge: dict[str, KnownConcept] | None = None,
    session: Session | None = None,
    paper: PaperMeta | None = None,
) -> Any:
    from papermind.agent.loop import TurnContext

    return TurnContext(
        session=session or make_session(level=level),
        user=make_user(level=level),
        paper=paper or make_paper(),
        question=question,
        quote=quote,
        level=level,  # type: ignore[arg-type]
        known_summary=list(known),
        knowledge=knowledge,
    )


class EventLog:
    """An ``emit`` that records events; ``types`` gives the stream as the client sees it."""

    def __init__(self) -> None:
        self.events: list[ChatEvent] = []

    async def __call__(self, event: ChatEvent) -> None:
        self.events.append(event)

    @property
    def types(self) -> list[str]:
        return [e.type for e in self.events]

    def of(self, type_: str) -> list[Any]:
        return [e for e in self.events if e.type == type_]


def make_runner(
    llm: FakeLLM,
    mcp: FakeMCP | None = None,
    *,
    mastery: bool = True,
    knowledge_source: Any = None,
    **settings_overrides: Any,
) -> Any:
    """An ``AgentRunner`` over the fakes; ``knowledge_source`` defaults to the fake graph's."""
    from papermind.agent.loop import AgentRunner
    from papermind.agent.mastery import MasteryPass
    from papermind.agent.registry import build_registry
    from papermind.agent.writer import make_writer

    mcp = mcp or FakeMCP()
    settings = fake_settings(**settings_overrides)
    return AgentRunner(
        llm=llm,
        mcp=mcp,
        registry=build_registry(),
        writer=make_writer(settings, llm),
        settings=settings,
        mastery=MasteryPass(llm=llm, mcp=mcp, settings=settings) if mastery else None,
        knowledge_source=knowledge_source if knowledge_source is not None else mcp.graph.known,
    )
