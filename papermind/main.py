"""Application entry point: ``uvicorn papermind.main:create_app --factory --port 8000``.

The lifespan opens every resource the routes use and closes them in reverse
order on shutdown. Each dependency degrades rather than stopping the app where
that is safe, and ``GET /api/health`` reports what is degraded:

* Redis unreachable: sessions and ingest progress fall back to this process's
  memory in dev (lost on restart, not shared between workers). Production
  refuses to start, because a second worker could not see the first's sessions.
* Neo4j unreachable: ``get_graph_store`` falls back to the in-memory graph in
  dev, and the prereq_graph MCP server then runs inside this process on that
  same store (see ``prereq_graph_transport``). Production refuses to start, for
  the same reason as Redis: each worker would hold its own readers.
* An MCP server that fails to start is logged; its tools report themselves
  unavailable and the manager retries it on the next call.
* No Anthropic key: no model client and no agent. Chat answers with a
  ``no_api_key`` error event; suggestions come from section titles.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import re
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI
from mcp.client._memory import InMemoryTransport
from redis.asyncio import Redis
from redis.exceptions import RedisError

from mcp_servers.prereq_graph.server import create_server as create_prereq_server
from papermind.agent.loop import build_runner
from papermind.api.app import build_app
from papermind.api.arxiv_check import ArxivLookup
from papermind.api.background import TaskSet
from papermind.api.chat_turn import read_knowledge
from papermind.api.deps import AsyncPapers, Resources
from papermind.api.ingest_jobs import IngestJobs
from papermind.api.suggestions import SuggestionService
from papermind.config import Settings, get_settings
from papermind.graph.store import GraphStore, get_graph_store
from papermind.ingest.pipeline import Ingestor
from papermind.ingest.store import PaperStore
from papermind.llm.client import LLM
from papermind.mcp_client.manager import MCPManager, Transport, default_transports
from papermind.memory.ingest_status import MemoryIngestStatusStore, RedisIngestStatusStore
from papermind.memory.session_store import MemorySessionStore, RedisSessionStore
from papermind.memory.users import CatalogSource
from papermind.retrieval.dense import EmbeddingCache, FastEmbedEmbedder

log = logging.getLogger("papermind")

REDIS_CONNECT_TIMEOUT_S = 2.0
# Libraries that log every request at INFO.
_QUIET_LOGGERS = ("httpx", "httpcore", "neo4j", "mcp", "aiohttp.access")


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root.setLevel(level.upper())
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def redact_url(url: str) -> str:
    """The URL with any credentials removed, for log lines.

    Greedy up to the *last* '@', because that is where URL parsers (and so
    redis-py) end the userinfo: a password may itself contain '@'. An '@'
    later in the URL costs the host in the log line, never the password.
    """
    return re.sub(r"//.*@", "//***@", url, count=1)


async def connect_redis(settings: Settings) -> Redis | None:
    """A connected client, or None in dev when Redis is unreachable. Raises in prod."""
    client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_S,
        health_check_interval=30,
    )
    try:
        async with asyncio.timeout(REDIS_CONNECT_TIMEOUT_S + 1):
            await client.ping()
    except (RedisError, OSError, TimeoutError) as exc:
        await client.aclose()
        where = redact_url(settings.redis_url)
        if settings.environment == "prod":
            raise RuntimeError(
                f"Redis at {where} is unreachable. Production needs it: sessions and ingest "
                "progress must be shared by every worker."
            ) from exc
        log.warning(
            "Redis at %s is unreachable (%s); sessions and ingest progress are kept in this "
            "process's memory, lost on restart and not shared between workers",
            where,
            type(exc).__name__,
        )
        return None
    return client


def prereq_graph_transport(
    settings: Settings, graph: asyncio.Future[GraphStore], separate: Transport
) -> Transport:
    """How to reach prereq_graph, decided once this process's graph store is open.

    With Neo4j, the server (its own process) reads and writes the same database
    as the API. Without it, that process would fall back to an in-memory store
    of its own: the agent's check_prerequisites would not see what the reader
    told the API they know, and the mastery pass's writes would never show up
    in (or be undone through) the knowledge routes. So the in-memory store gets
    a single owner: the server runs in this process, on the API's own store.
    """

    @asynccontextmanager
    async def connect() -> AsyncIterator[Any]:
        store = await graph
        if store.backend == "neo4j":
            async with separate() as streams:
                yield streams
            return
        log.warning(
            "prereq_graph runs inside the API process on the in-memory graph store, "
            "so the agent and the knowledge routes share one copy of each reader's knowledge"
        )
        async with InMemoryTransport(create_prereq_server(store, settings=settings)) as streams:
            yield streams

    return connect


@asynccontextmanager
async def open_resources(settings: Settings) -> AsyncIterator[Resources]:
    """Every resource the API uses, opened in dependency order and closed in reverse."""
    async with AsyncExitStack() as stack:
        graph_ready: asyncio.Future[GraphStore] = asyncio.get_running_loop().create_future()
        transports = default_transports(settings)
        transports["prereq_graph"] = prereq_graph_transport(settings, graph_ready, transports["prereq_graph"])
        mcp = MCPManager(settings, transports=transports)
        stack.push_async_callback(mcp.aclose)
        # Unwinds before mcp.aclose: if the graph never opened, the waiting
        # prereq_graph connection gives up instead of holding shutdown.
        stack.callback(graph_ready.cancel)
        # The slowest start (the retriever may load its embedding model before
        # answering), so it runs while Redis and Neo4j connect.
        mcp_started = asyncio.create_task(mcp.start(), name="mcp:start")
        stack.callback(mcp_started.cancel)

        redis = await connect_redis(settings)
        if redis is not None:
            stack.push_async_callback(redis.aclose)
            sessions: RedisSessionStore | MemorySessionStore = RedisSessionStore(
                redis, ttl_seconds=settings.session_ttl_seconds
            )
            ingest_status: RedisIngestStatusStore | MemoryIngestStatusStore = RedisIngestStatusStore(redis)
        else:
            sessions = MemorySessionStore(ttl_seconds=settings.session_ttl_seconds)
            ingest_status = MemoryIngestStatusStore()

        graph = await get_graph_store(settings)
        stack.push_async_callback(graph.aclose)
        if graph.backend != "neo4j" and settings.environment == "prod":
            raise RuntimeError(
                f"Neo4j at {redact_url(settings.neo4j_uri)} is unreachable. Production needs it: "
                "reader knowledge must survive restarts and be shared by every worker."
            )
        graph_ready.set_result(graph)

        await mcp_started

        llm = LLM(settings) if settings.has_api_key else None
        runner = None
        if llm is not None:
            stack.push_async_callback(llm.aclose)
            # Raises RegistryMismatch when a server's tool schema no longer
            # matches what the agent sends: a deploy bug, so fail at startup.
            runner = build_runner(
                settings, llm=llm, mcp=mcp, knowledge_source=functools.partial(read_knowledge, graph)
            )
        else:
            log.warning("ANTHROPIC_API_KEY is not set: chat is disabled until it is added to .env")

        store = PaperStore(settings.papers_dir)
        embedding_cache = settings.cache_dir / "embeddings"
        papers = AsyncPapers(store, derived_dirs=[embedding_cache])
        # From this app's settings rather than the process-wide defaults, so an
        # app built on another data_dir (the integration tests) stays inside it.
        # The model itself loads lazily, on the first ingestion.
        ingestor = Ingestor(
            store,
            embedder=FastEmbedEmbedder(settings.embedding_model, settings.data_dir / "models"),
            cache=EmbeddingCache(embedding_cache),
            max_chunk_tokens=settings.max_chunk_tokens,
            max_upload_bytes=settings.max_upload_mb * 1024 * 1024,
        )
        jobs = IngestJobs(
            ingest_status,
            ingest_arxiv=ingestor.ingest_arxiv,
            ingest_upload=ingestor.ingest_upload,
            discard=papers.discard,
        )
        stack.push_async_callback(jobs.aclose)
        background = TaskSet()
        stack.push_async_callback(background.aclose)

        yield Resources(
            settings=settings,
            sessions=sessions,
            ingest_status=ingest_status,
            papers=papers,
            graph=graph,
            jobs=jobs,
            suggestions=SuggestionService(llm, effort=settings.classifier_effort),
            catalog=CatalogSource(settings.concepts_path),
            redis=redis,
            mcp=mcp,
            runner=runner,
            llm=llm,
            prewarm=runner.prewarm if runner is not None else None,
            arxiv=ArxivLookup(),
            background=background,
        )
        log.info("shutting down")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_resources(settings) as resources:
            app.state.resources = resources
            log.info(
                "PaperMind %s ready (sessions: %s, graph: %s, chat: %s)",
                settings.app_version,
                resources.sessions.backend,
                resources.graph.backend,
                "on" if resources.runner is not None else "off (no API key)",
            )
            yield

    return build_app(settings, lifespan=lifespan)
