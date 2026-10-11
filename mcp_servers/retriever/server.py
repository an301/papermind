"""MCP server: passage retrieval over one ingested paper.

Tool (parameters are exactly ``SearchPaperServerArgs`` from
``papermind/schemas/tools.py``; the result is ``SearchPaperResult``):

* ``search_paper`` -- BM25 + dense (FAISS) fused with RRF, or either arm alone

The embedding model is loaded and run once in the lifespan, before the first
request, so the agent's first search of a session is not the one that pays the
model load. Per-paper indexes are built on first use and cached in this process
(see ``papermind.retrieval.index``); ingestion has normally already computed
the chunk vectors, so building one is a file read.

Run: ``python -m mcp_servers.retriever.server`` (stdio) or add
``--http --port 8101`` for streamable HTTP at ``/mcp``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from papermind.config import Settings, get_settings
from papermind.ingest.store import PaperStore
from papermind.retrieval.dense import EmbeddingCache, get_embedder
from papermind.retrieval.hybrid import HybridRetriever
from papermind.retrieval.index import IndexRegistry, PaperNotFound
from papermind.schemas.tools import SearchPaperResult, SearchPaperServerArgs

SERVER_NAME = "papermind-retriever"
DEFAULT_HTTP_PORT = 8101

log = logging.getLogger(__name__)

# Tool parameters reuse the schema's own FieldInfo (constraints, descriptions,
# defaults), so the advertised input schema cannot drift from
# SearchPaperServerArgs; tests/unit/test_retrieval_mcp_server.py asserts it.
_ARGS = SearchPaperServerArgs.model_fields
_Mode = _ARGS["mode"].annotation


class _RetrieverSlot:
    """The retriever the tool uses: injected (tests) or built by the server lifespan."""

    def __init__(self, retriever: HybridRetriever | None) -> None:
        self.retriever = retriever

    def get(self) -> HybridRetriever:
        if self.retriever is None:
            raise RuntimeError("retriever is not ready (server lifespan not entered)")
        return self.retriever


def build_retriever(settings: Settings) -> HybridRetriever:
    registry = IndexRegistry(
        PaperStore(settings.papers_dir),
        get_embedder(),
        cache=EmbeddingCache(settings.cache_dir / "embeddings"),
    )
    return HybridRetriever(registry, rrf_k=settings.rrf_k)


async def warm(retriever: HybridRetriever) -> None:
    """Load the embedding model and run one query through it, off the event loop."""
    started = time.perf_counter()
    embedder = retriever.registry.embedder
    await asyncio.to_thread(embedder.embed_query, "warm-up")
    log.info("retriever warm: %s ready in %.1fs", embedder.name, time.perf_counter() - started)


def create_server(
    retriever: HybridRetriever | None = None, *, settings: Settings | None = None, warm_up: bool = True
) -> MCPServer:
    """Build the server. With ``retriever`` given the caller owns it; otherwise
    the lifespan builds one over ``settings.papers_dir``."""
    settings = settings or get_settings()
    slot = _RetrieverSlot(retriever)
    owns_retriever = retriever is None

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        if owns_retriever:
            slot.retriever = build_retriever(settings)
        if warm_up:
            await warm(slot.get())
        try:
            yield
        finally:
            if owns_retriever:
                slot.retriever = None

    server = MCPServer(
        SERVER_NAME,
        instructions="Passage retrieval (BM25 + dense, fused with RRF) over a paper ingested into PaperMind.",
        lifespan=lifespan,
    )

    @server.tool(
        description=(
            "Search one ingested paper for the passages that answer a query. Returns the top k chunks "
            "with their section, pages, fused rank and score, and each arm's own rank."
        )
    )
    async def search_paper(
        paper_id: Annotated[str, _ARGS["paper_id"]],
        query: Annotated[str, _ARGS["query"]],
        k: Annotated[int, _ARGS["k"]] = 5,
        section_filter: Annotated[str | None, _ARGS["section_filter"]] = None,
        mode: Annotated[_Mode, _ARGS["mode"]] = "hybrid",  # type: ignore[valid-type]
    ) -> SearchPaperResult:
        try:
            chunks = await slot.get().search(query, paper_id, k=k, mode=mode, section_filter=section_filter)
        except PaperNotFound as exc:
            raise ToolError(
                f"Unknown paper_id {paper_id!r}: no ingested paper has this id. "
                "Use the paper_id of a paper in the library."
            ) from exc
        return SearchPaperResult(paper_id=paper_id, query=query, mode=mode, chunks=chunks)

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    args = parser.parse_args(argv)

    settings = get_settings()
    # stdout is the protocol channel under stdio; logs must go to stderr.
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = create_server(settings=settings)
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
