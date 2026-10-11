"""MCP server: the prerequisite graph and readers' knowledge.

Tools (parameters are exactly the ``*ServerArgs`` fields in
``papermind/schemas/tools.py``; results are the matching ``*Result`` models):

* ``check_prerequisites`` -- which prerequisites of a concept this reader lacks
* ``get_learning_path``   -- the same gaps, in the order to teach them
* ``mark_known``          -- write a KNOWS edge (mastery pass / knowledge API only)
* ``forget_concept``      -- delete a KNOWS edge

``mark_known`` and ``forget_concept`` are the only writes of persistent reader
state in the system. They are not agent-visible (the registry does not route
to them); the mastery pass calls them after the answer, and the UI can undo.

Run: ``python -m mcp_servers.prereq_graph.server`` (stdio) or add
``--http --port 8102`` for streamable HTTP.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from mcp.server.mcpserver import MCPServer

from papermind.config import Settings, get_settings
from papermind.graph import service
from papermind.graph.store import GraphStore, get_graph_store
from papermind.schemas.tools import (
    CheckPrerequisitesResult,
    CheckPrerequisitesServerArgs,
    ForgetConceptServerArgs,
    LearningPathResult,
    LearningPathServerArgs,
    MarkKnownResult,
    MarkKnownServerArgs,
)

SERVER_NAME = "papermind-prereq-graph"
DEFAULT_HTTP_PORT = 8102

log = logging.getLogger(__name__)

# Tool parameters reuse the schema's own FieldInfo (constraints, descriptions,
# defaults), so the advertised input schema cannot drift from *ServerArgs.
# tests/unit/test_graph_mcp_server.py asserts the two schemas are identical.
_CHECK = CheckPrerequisitesServerArgs.model_fields
_PATH = LearningPathServerArgs.model_fields
_MARK = MarkKnownServerArgs.model_fields
_FORGET = ForgetConceptServerArgs.model_fields
# The schema declares the KNOWS provenance Literal inline; borrow it rather than
# restating the three values here.
_Source = _MARK["source"].annotation


class _StoreSlot:
    """The store the tools use: injected (tests) or opened by the server lifespan."""

    def __init__(self, store: GraphStore | None) -> None:
        self.store = store

    def get(self) -> GraphStore:
        if self.store is None:
            raise RuntimeError("graph store is not open (server lifespan not entered)")
        return self.store


def create_server(store: GraphStore | None = None, *, settings: Settings | None = None) -> MCPServer:
    """Build the server. With ``store`` given the caller owns its lifecycle;
    otherwise the lifespan opens one with ``get_graph_store`` and closes it."""
    settings = settings or get_settings()
    slot = _StoreSlot(store)
    owns_store = store is None

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        if owns_store:
            slot.store = await get_graph_store(settings)
            log.info("prereq_graph using the %s graph store", slot.store.backend)
        try:
            yield
        finally:
            if owns_store and slot.store is not None:
                await slot.store.aclose()
                slot.store = None

    server = MCPServer(
        SERVER_NAME,
        instructions="Prerequisite concept graph and per-reader knowledge for PaperMind.",
        lifespan=lifespan,
    )

    @server.tool(
        description=(
            "Prerequisites of a concept the reader does not know yet, in BFS order from the concept, "
            "plus the explanation altitude (technical / scaffolded / intuition)."
        )
    )
    async def check_prerequisites(
        concept: Annotated[str, _CHECK["concept"]],
        user_id: Annotated[str, _CHECK["user_id"]],
    ) -> CheckPrerequisitesResult:
        return await service.check_prerequisites(
            slot.get(), user_id, concept, known_threshold=settings.known_threshold
        )

    @server.tool(description="The reader's missing prerequisites in teaching order, the concept itself last.")
    async def get_learning_path(
        concept: Annotated[str, _PATH["concept"]],
        user_id: Annotated[str, _PATH["user_id"]],
    ) -> LearningPathResult:
        return await service.learning_path(
            slot.get(), user_id, concept, known_threshold=settings.known_threshold
        )

    @server.tool(description="Record that the reader knows a concept (creates or overwrites the KNOWS edge).")
    async def mark_known(
        user_id: Annotated[str, _MARK["user_id"]],
        concept: Annotated[str, _MARK["concept"]],
        confidence: Annotated[float, _MARK["confidence"]],
        source: Annotated[_Source, _MARK["source"]],  # type: ignore[valid-type]
        evidence: Annotated[str, _MARK["evidence"]] = "",
    ) -> MarkKnownResult:
        args = MarkKnownServerArgs(
            user_id=user_id, concept=concept, confidence=confidence, source=source, evidence=evidence
        )
        return await slot.get().mark(args)

    @server.tool(description="Remove the reader's KNOWS edge for a concept.")
    async def forget_concept(
        user_id: Annotated[str, _FORGET["user_id"]],
        concept: Annotated[str, _FORGET["concept"]],
    ) -> MarkKnownResult:
        return await slot.get().forget(user_id, concept)

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
