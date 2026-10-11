"""Typed access to the resources the lifespan puts on ``app.state``.

Routes declare what they need (``graph: GraphDep``) instead of reaching into
``request.app.state`` with untyped attribute access, and tests swap any
resource by building their own ``Resources``.

The protocols below are the slice of each collaborator the API actually uses,
so the API can be exercised with small fakes and does not import the agent,
LLM or MCP stacks just to type-check.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import Depends, Request
from redis.asyncio import Redis

from papermind.api.arxiv_check import ArxivLookupAPI
from papermind.api.background import TaskSet
from papermind.api.ingest_jobs import IngestJobs
from papermind.api.suggestions import StructuredLLM, SuggestionService
from papermind.config import Settings
from papermind.graph.store import GraphStore
from papermind.ingest.store import validate_paper_id
from papermind.memory.ingest_status import IngestStatusStore
from papermind.memory.session_store import SessionStore
from papermind.memory.users import CatalogSource
from papermind.schemas.api import ServiceCheck
from papermind.schemas.events import ChatEvent
from papermind.schemas.paper import Chunk, PaperMeta, PaperRecord

Emit = Callable[[ChatEvent], Awaitable[None]]


class PaperStoreAPI(Protocol):
    """``papermind.ingest.store.PaperStore``: synchronous file I/O, called via ``AsyncPapers``."""

    def load(self, paper_id: str) -> PaperRecord | None: ...
    def meta(self, paper_id: str) -> PaperMeta | None: ...
    def pdf_path(self, paper_id: str) -> Path: ...
    def list(self) -> list[PaperMeta]: ...
    def delete(self, paper_id: str) -> None: ...
    def chunk(self, paper_id: str, chunk_id: str) -> Chunk | None: ...


class TurnRunner(Protocol):
    """``papermind.agent.loop.AgentRunner``."""

    async def run_turn(self, ctx: Any, emit: Emit) -> Any: ...


class MCPStatus(Protocol):
    """``papermind.mcp_client.manager.MCPManager``, as far as health checks need it."""

    async def health(self) -> dict[str, ServiceCheck]:
        """``mcp:<server>`` -> reachable or not."""
        ...


Prewarm = Callable[[PaperMeta], Coroutine[Any, Any, Any]]


class AsyncPapers:
    """The paper store with every call moved off the event loop.

    ``paper.json`` for a long paper is a few MB of JSON; parsing it inline
    would stall every other request on this worker.

    ``derived_dirs`` are roots that hold per-paper data derived from the paper
    (``{root}/{paper_id}/``, e.g. the embedding cache); ``discard`` removes
    those too, so a removed paper leaves nothing behind.
    """

    def __init__(self, store: PaperStoreAPI, *, derived_dirs: Sequence[Path] = ()) -> None:
        self.store = store
        self._derived_dirs = tuple(derived_dirs)

    async def meta(self, paper_id: str) -> PaperMeta | None:
        return await asyncio.to_thread(self.store.meta, paper_id)

    async def load(self, paper_id: str) -> PaperRecord | None:
        return await asyncio.to_thread(self.store.load, paper_id)

    async def list(self) -> list[PaperMeta]:
        return await asyncio.to_thread(self.store.list)

    async def chunk(self, paper_id: str, chunk_id: str) -> Chunk | None:
        return await asyncio.to_thread(self.store.chunk, paper_id, chunk_id)

    async def delete(self, paper_id: str) -> None:
        await asyncio.to_thread(self.store.delete, paper_id)

    async def discard(self, paper_id: str) -> None:
        """Delete the paper and everything derived from it. Idempotent."""
        validate_paper_id(paper_id)  # it becomes a path below
        await self.delete(paper_id)
        for root in self._derived_dirs:
            await asyncio.to_thread(shutil.rmtree, root / paper_id, True)

    def pdf_path(self, paper_id: str) -> Path:
        return self.store.pdf_path(paper_id)

    def paper_dir(self, paper_id: str) -> Path:
        return self.store.pdf_path(paper_id).parent


@dataclass
class Resources:
    settings: Settings
    sessions: SessionStore
    ingest_status: IngestStatusStore
    papers: AsyncPapers
    graph: GraphStore
    jobs: IngestJobs
    suggestions: SuggestionService
    catalog: CatalogSource
    redis: Redis | None = None
    mcp: MCPStatus | None = None
    runner: TurnRunner | None = None
    llm: StructuredLLM | None = None
    prewarm: Prewarm | None = None
    # None skips the up-front arXiv check: the ingestion still fails on a bad
    # id, just later and on the library row instead of in the hint line.
    arxiv: ArxivLookupAPI | None = None
    background: TaskSet = field(default_factory=TaskSet)


def get_resources(request: Request) -> Resources:
    resources: Resources = request.app.state.resources
    return resources


ResourcesDep = Annotated[Resources, Depends(get_resources)]


def _settings(res: ResourcesDep) -> Settings:
    return res.settings


def _sessions(res: ResourcesDep) -> SessionStore:
    return res.sessions


def _ingest_status(res: ResourcesDep) -> IngestStatusStore:
    return res.ingest_status


def _papers(res: ResourcesDep) -> AsyncPapers:
    return res.papers


def _graph(res: ResourcesDep) -> GraphStore:
    return res.graph


def _jobs(res: ResourcesDep) -> IngestJobs:
    return res.jobs


SettingsDep = Annotated[Settings, Depends(_settings)]
SessionsDep = Annotated[SessionStore, Depends(_sessions)]
IngestStatusDep = Annotated[IngestStatusStore, Depends(_ingest_status)]
PapersDep = Annotated[AsyncPapers, Depends(_papers)]
GraphDep = Annotated[GraphStore, Depends(_graph)]
JobsDep = Annotated[IngestJobs, Depends(_jobs)]
