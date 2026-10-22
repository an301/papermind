"""Shared fakes and builders for the API and memory unit tests (no services, no network, no key).

Collected by pytest like any test module but defines no tests; it lives under
the ``test_api_*`` name so it sits with the tests that import it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import fakeredis
import httpx
from fastapi import FastAPI

from papermind.api.app import build_app
from papermind.api.deps import READER_HEADER, AsyncPapers, Resources
from papermind.api.ingest_jobs import IngestJobs, ProgressFn
from papermind.api.suggestions import SuggestionService
from papermind.config import Settings
from papermind.graph.store import InMemoryGraphStore
from papermind.graph.testing import sample_catalog
from papermind.ingest.arxiv import ArxivMetadata, ArxivNotFound
from papermind.memory.ingest_status import RedisIngestStatusStore
from papermind.memory.paper_readers import PaperReaders
from papermind.memory.session_store import RedisSessionStore
from papermind.memory.users import CatalogSource
from papermind.schemas.events import (
    AnswerDelta,
    AnswerDone,
    ChatEvent,
    Done,
    GapsFound,
    KnowledgeUpdated,
    SourceRef,
    SourcesReady,
    ToolFinished,
    ToolStarted,
    TurnStarted,
    UsageReport,
)
from papermind.schemas.paper import Box, Chunk, IngestStage, PaperMeta, PaperRecord, Section
from papermind.schemas.session import UserProfile
from papermind.schemas.tools import ConceptRef, Gap

PAPER_ID = "1706.03762"
READER_ID = "u_reader"
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n" + b"0123456789" * 50


def make_record(paper_id: str = PAPER_ID, *, title: str = "Attention Is All You Need") -> PaperRecord:
    sections = [
        Section(section_id="sec_abstract", title="Abstract", page=0, y=200.0),
        Section(section_id="sec_1", number="1", title="Introduction", page=0, y=400.0),
        Section(section_id="sec_3", number="3", title="Model Architecture", page=2, y=80.0),
        Section(
            section_id="sec_3.2", number="3.2", title="Scaled Dot-Product Attention", level=2, page=3, y=120.0
        ),
        Section(section_id="sec_6", number="6", title="Results", page=7, y=90.0),
    ]
    chunks = [
        Chunk(
            chunk_id=f"{paper_id}:sec_3.2_scaled_dot_product_attention",
            paper_id=paper_id,
            section_id="sec_3.2",
            section_number="3.2",
            section_title="Scaled Dot-Product Attention",
            text="We call our particular attention Scaled Dot-Product Attention. " * 8,
            token_count=96,
            page_start=3,
            page_end=3,
            boxes=[Box(page=3, x0=72, y0=120, x1=540, y1=300)],
        ),
        Chunk(
            chunk_id=f"{paper_id}:sec_6_results",
            paper_id=paper_id,
            section_id="sec_6",
            section_number="6",
            section_title="Results",
            text="On the WMT 2014 English-to-German translation task the big model reaches 28.4 BLEU. " * 6,
            token_count=110,
            page_start=7,
            page_end=8,
        ),
    ]
    meta = PaperMeta(
        paper_id=paper_id,
        source="upload" if paper_id.startswith("up_") else "arxiv",
        arxiv_id=None if paper_id.startswith("up_") else paper_id,
        arxiv_version=None if paper_id.startswith("up_") else "v7",
        title=title,
        authors=["Ashish Vaswani", "Noam Shazeer"],
        abstract="The dominant sequence transduction models are based on recurrent networks...",
        published=date(2017, 6, 12),
        num_pages=15,
        page_sizes=[(612.0, 792.0)] * 15,
        sections=sections,
        chunk_count=len(chunks),
        chunker_version="1",
        ingested_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    )
    return PaperRecord(meta=meta, chunks=chunks)


class FilePaperStore:
    """The ``PaperStore`` contract over a directory (data/papers/{paper_id}/paper.json + source.pdf)."""

    def __init__(self, papers_dir: Path) -> None:
        self.papers_dir = papers_dir
        papers_dir.mkdir(parents=True, exist_ok=True)

    def _dir(self, paper_id: str) -> Path:
        return self.papers_dir / paper_id

    def save_pdf(self, paper_id: str, pdf: bytes) -> None:
        self._dir(paper_id).mkdir(parents=True, exist_ok=True)
        self.pdf_path(paper_id).write_bytes(pdf)

    def save(self, record: PaperRecord, pdf: bytes) -> None:
        directory = self._dir(record.meta.paper_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "source.pdf").write_bytes(pdf)
        (directory / "paper.json").write_text(record.model_dump_json(), encoding="utf-8")

    def load(self, paper_id: str) -> PaperRecord | None:
        path = self._dir(paper_id) / "paper.json"
        return PaperRecord.model_validate_json(path.read_text()) if path.is_file() else None

    def meta(self, paper_id: str) -> PaperMeta | None:
        record = self.load(paper_id)
        return record.meta if record else None

    def pdf_path(self, paper_id: str) -> Path:
        return self._dir(paper_id) / "source.pdf"

    def list(self) -> list[PaperMeta]:
        metas = [self.meta(d.name) for d in sorted(self.papers_dir.iterdir()) if d.is_dir()]
        return [m for m in metas if m is not None]

    def delete(self, paper_id: str) -> None:
        shutil.rmtree(self._dir(paper_id), ignore_errors=True)

    def chunk(self, paper_id: str, chunk_id: str) -> Any:
        record = self.load(paper_id)
        return next((c for c in record.chunks if c.chunk_id == chunk_id), None) if record else None


@dataclass
class FakeIngest:
    """Stands in for ingest_arxiv / ingest_upload: reports every stage, then saves a record."""

    store: FilePaperStore
    gate: asyncio.Event = field(default_factory=asyncio.Event)
    fail_with: BaseException | None = None
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.gate.set()

    async def _run(
        self, paper_id: str, title: str, pdf: bytes, on_progress: ProgressFn | None
    ) -> PaperRecord:
        for stage in (IngestStage.DOWNLOADING, IngestStage.EXTRACTING, IngestStage.CHUNKING):
            if on_progress:
                await on_progress(stage, 0.5)
        await self.gate.wait()
        if self.fail_with is not None:
            raise self.fail_with
        record = make_record(paper_id, title=title)
        self.store.save(record, pdf)
        if on_progress:
            await on_progress(IngestStage.INDEXING, 1.0)
        return record

    async def arxiv(self, raw: str, *, on_progress: ProgressFn | None = None) -> PaperRecord:
        self.calls.append(("arxiv", raw))
        paper_id = raw.rsplit("/", 1)[-1].split("v")[0]
        return await self._run(paper_id, f"arXiv paper {paper_id}", MINIMAL_PDF, on_progress)

    async def upload(
        self, pdf: bytes, filename: str, *, on_progress: ProgressFn | None = None
    ) -> PaperRecord:
        import hashlib

        self.calls.append(("upload", filename))
        paper_id = "up_" + hashlib.sha256(pdf).hexdigest()[:12]
        return await self._run(paper_id, filename, pdf, on_progress)


Script = Callable[[Any, Callable[[ChatEvent], Awaitable[None]]], Awaitable[Any]]


class FakeRunner:
    """An AgentRunner that emits a scripted turn and records the contexts it was given."""

    def __init__(self, script: Script | None = None) -> None:
        self.contexts: list[Any] = []
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self._script = script or happy_turn

    async def run_turn(self, ctx: Any, emit: Callable[[ChatEvent], Awaitable[None]]) -> Any:
        self.contexts.append(ctx)
        self.started.set()
        try:
            return await self._script(ctx, emit)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


async def happy_turn(ctx: Any, emit: Callable[[ChatEvent], Awaitable[None]]) -> None:
    # Framed like the real AgentRunner, which emits its own turn.started and
    # done; the API must drop both and frame the stream itself.
    await emit(
        TurnStarted(
            turn_id=ctx.turn_id,
            session_id=ctx.session.session_id,
            level=ctx.level,
            user_message_id=ctx.user_message_id,
        )
    )
    await emit(
        ToolStarted(
            call_id="c1", tool="search_paper", label="Searching the paper", args={"query": "attention"}
        )
    )
    await emit(
        ToolFinished(call_id="c1", tool="search_paper", ok=True, duration_ms=12, summary="Found 2 passages")
    )
    await emit(
        GapsFound(
            concept=ConceptRef(concept_id="attention", name="Attention"),
            gaps=[Gap(concept_id="softmax", name="Softmax", depth=1)],
            teaching_order=[ConceptRef(concept_id="softmax", name="Softmax")],
            altitude="technical",
        )
    )
    await emit(
        SourcesReady(
            sources=[
                SourceRef(
                    label="S1",
                    chunk_id=f"{PAPER_ID}:sec_3.2_scaled_dot_product_attention",
                    section_number="3.2",
                    section_title="Scaled Dot-Product Attention",
                    page_start=3,
                    page_end=3,
                    snippet="We call our particular attention...",
                )
            ]
        )
    )
    await emit(AnswerDelta(text="Attention weighs "))
    await emit(AnswerDelta(text="values [S1]."))
    await emit(
        AnswerDone(message_id="m_answer", text="Attention weighs values [S1].", cited=["S1"], writer="fake")
    )
    await emit(KnowledgeUpdated(added=[ConceptRef(concept_id="attention", name="Attention")]))
    await emit(
        UsageReport(
            model_calls=2,
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            cost_usd=0.0,
            latency_ms=5,
        )
    )
    await emit(Done())


class FakeArxivLookup:
    """``ArxivLookupAPI`` over a fixed table: arxiv_id -> latest metadata, or the exception to raise."""

    def __init__(self, papers: dict[str, ArxivMetadata | BaseException] | None = None) -> None:
        self.papers = papers or {}
        self.calls: list[str] = []

    async def latest(self, arxiv_id: str) -> ArxivMetadata:
        self.calls.append(arxiv_id)
        found = self.papers.get(arxiv_id)
        if found is None:
            raise ArxivNotFound(f"arXiv has no paper {arxiv_id}")
        if isinstance(found, BaseException):
            raise found
        return found


def arxiv_meta(
    arxiv_id: str = "1409.0473",
    version: str = "v7",
    *,
    title: str = "Neural Machine Translation by Jointly Learning to Align and Translate",
    abstract: str = "Neural machine translation is a recently proposed approach to machine translation.",
) -> ArxivMetadata:
    return ArxivMetadata(
        arxiv_id=arxiv_id,
        version=version,
        title=title,
        authors=["Dzmitry Bahdanau", "Kyunghyun Cho", "Yoshua Bengio"],
        abstract=abstract,
        published=date(2014, 9, 1),
    )


@dataclass
class FakeParse:
    parsed: Any


class FakeLLM:
    def __init__(self, questions: list[str] | None = None, *, fail: bool = False) -> None:
        self.questions = questions or [
            "How does scaled dot-product attention work?",
            "Why divide by the square root of d_k?",
            "What does multi-head attention add?",
            "How does the model encode word order?",
        ]
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> FakeParse:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("model unavailable")
        return FakeParse(parsed=kwargs["schema"](questions=self.questions))


class FakeMCP:
    def __init__(self, down: set[str] | None = None) -> None:
        self.down = down or set()

    async def health(self) -> dict[str, Any]:
        from papermind.schemas.api import ServiceCheck

        return {
            f"mcp:{name}": ServiceCheck(
                ok=name not in self.down, detail=None if name not in self.down else "down"
            )
            for name in ("retriever", "prereq_graph", "semantic_scholar")
        }


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "anthropic_api_key": None,
        "environment": "test",
        "prewarm_cache": False,
        **overrides,
    }
    # _env_file=None: a developer's .env (with a real key) must not leak into unit tests.
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@dataclass
class Harness:
    app: FastAPI
    res: Resources
    store: FilePaperStore
    ingest: FakeIngest
    redis: Any
    graph: InMemoryGraphStore

    def client(self, reader_id: str | None = READER_ID) -> httpx.AsyncClient:
        """A browser: it sends its reader id, as the frontend does, unless given None."""
        headers = {READER_HEADER: reader_id} if reader_id else {}
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test", headers=headers
        )

    async def add_user(self, level: str = "intermediate") -> UserProfile:
        return await self.graph.upsert_user(
            UserProfile(user_id=READER_ID, level=level, created_at=datetime.now(UTC))  # type: ignore[arg-type]
        )


def make_harness(
    tmp_path: Path,
    *,
    runner: Any = None,
    llm: Any = None,
    mcp: Any = None,
    with_paper: bool = True,
    settings: Settings | None = None,
    graph: InMemoryGraphStore | None = None,
) -> Harness:
    settings = settings or make_settings(tmp_path)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = FilePaperStore(settings.papers_dir)
    if with_paper:
        store.save(make_record(), MINIMAL_PDF)
    graph = graph or InMemoryGraphStore(sample_catalog())
    status = RedisIngestStatusStore(redis)
    ingest = FakeIngest(store)
    concepts_dir = settings.data_dir / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    (concepts_dir / "concepts.json").write_text(json.dumps({"version": 1, "concepts": []}))
    readers = PaperReaders(settings.paper_readers_dir)
    papers = AsyncPapers(store, derived_dirs=[readers.root])
    res = Resources(
        settings=settings,
        sessions=RedisSessionStore(redis, ttl_seconds=settings.session_ttl_seconds),
        ingest_status=status,
        papers=papers,
        graph=graph,
        jobs=IngestJobs(
            status, ingest_arxiv=ingest.arxiv, ingest_upload=ingest.upload, discard=papers.discard
        ),
        suggestions=SuggestionService(llm),
        catalog=CatalogSource(settings.concepts_path),
        readers=readers,
        redis=redis,
        mcp=mcp,
        runner=runner,
        llm=llm,
    )
    app = build_app(settings, resources=res)
    return Harness(app=app, res=res, store=store, ingest=ingest, redis=redis, graph=graph)


def parse_sse(body: str) -> list[tuple[str | None, dict[str, Any] | None]]:
    """Frames as (event name, decoded data); comment-only frames come back as (None, None)."""
    frames: list[tuple[str | None, dict[str, Any] | None]] = []
    for raw in body.split("\n\n"):
        if not raw.strip():
            continue
        name: str | None = None
        data: list[str] = []
        for line in raw.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data.append(line[len("data: ") :])
        frames.append((name, json.loads("\n".join(data)) if data else None))
    return frames


def complete_frames(body: str) -> list[tuple[str | None, dict[str, Any] | None]]:
    """``parse_sse`` over the frames received so far (a trailing partial frame is ignored)."""
    complete, sep, _ = body.rpartition("\n\n")
    return parse_sse(complete + sep) if sep else []


@dataclass
class RawStream:
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""

    @property
    def frames(self) -> list[tuple[str | None, dict[str, Any] | None]]:
        return complete_frames(self.body)


async def drive_sse(
    app: Any,
    path: str,
    payload: dict[str, Any],
    *,
    disconnect_when: Callable[[list[tuple[str | None, dict[str, Any] | None]]], bool] | None = None,
    limit_s: float = 5.0,
) -> RawStream:
    """POST ``payload`` straight through the ASGI interface, optionally hanging up mid-stream.

    httpx's ASGITransport buffers the whole response and cannot disconnect, so
    disconnect behaviour is tested at this level. ASGI spec 2.3 makes Starlette
    watch ``receive()`` for ``http.disconnect``, as it does behind uvicorn.
    """
    out = RawStream()
    hang_up = asyncio.Event()
    sent_body = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}
        await hang_up.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            out.status = message["status"]
            out.headers = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            out.body += message.get("body", b"").decode()
            if disconnect_when is not None and disconnect_when(out.frames):
                hang_up.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
        "root_path": "",
    }
    await asyncio.wait_for(app(scope, receive, send), limit_s)
    return out
