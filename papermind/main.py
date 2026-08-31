"""FastAPI application factory and lifespan wiring.

Deliberately thin: it builds the app, mounts routers, and owns the lifespan that
opens and closes shared resources. Feature logic lives in the packages below
`papermind/`, so it stays testable without spinning up an ASGI app.

Startup builds the retrieval indexes for every ingested paper and caches them on
`app.state`. That is a few hundred milliseconds per paper once the embedding
model is loaded, and it moves the cost off the first user request. The local
Mistral is the opposite case -- tens of seconds and several GB -- so it stays
lazy; see `papermind/llm/local_mistral.py`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from papermind.config import Settings, get_settings
from papermind.retrieval import HybridRetriever, IndexRegistry

logger = logging.getLogger(__name__)

# Libraries that log every HTTP request at INFO. Loading the embedding model
# alone emits ~25 lines of huggingface cache traffic, which buries the startup
# messages that matter. Their warnings still come through.
_NOISY_LOGGERS = ("httpx", "httpcore", "huggingface_hub", "filelock", "urllib3")


def configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(levelname)s %(name)s: %(message)s")
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)

    registry = IndexRegistry(
        settings.papers_dir,
        embedding_model=settings.embedding_model,
    )
    app.state.index_registry = registry
    app.state.retriever = HybridRetriever(registry, rrf_k=settings.rrf_k)

    if settings.warm_indexes_at_startup:
        built = await registry.warm()
        logger.info("warmed %d paper index(es): %s", len(built), ", ".join(built) or "none")

    yield

    # Indexes are in-memory only; nothing to close yet. Redis, Neo4j, and the
    # MCP sessions will release here once they exist.


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI app.

    `settings` is injectable so tests can point at a fixture papers directory
    and skip index warming.
    """
    app = FastAPI(title="PaperMind", version="0.0.0", lifespan=lifespan)
    app.state.settings = settings or get_settings()

    @app.get("/health")
    async def health() -> dict[str, str]:
        # Liveness only. A readiness probe that pings Redis/Neo4j belongs in
        # papermind/api/routes/health.py once those clients exist.
        return {"status": "ok"}

    return app


app = create_app()
