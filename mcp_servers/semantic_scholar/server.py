"""MCP server: Semantic Scholar lookups, references, and citations for the agent.

Tools (parameters are exactly the ``*Args`` / ``*ServerArgs`` fields in
``papermind/schemas/tools.py``; results are the matching ``*Result`` models):

* ``lookup_paper``   -- a paper that is not the open one, by title, arXiv id, or DOI
* ``get_references`` -- what a paper cites, most-cited first
* ``get_citations``  -- what cites a paper (the most-cited of its newest citations)

This is the only server that depends on a third party, so its failures are
expected rather than exceptional: S2 rate-limits the keyless pool heavily and
has outages. Every failure becomes a tool error whose text tells the agent what
to do next (usually: answer from the paper alone), never a stack trace.

Run: ``python -m mcp_servers.semantic_scholar.server`` (stdio) or add
``--http --port 8103`` for streamable HTTP at ``/mcp``.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Annotated, Literal, TypeVar
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from papermind.config import Settings, get_settings
from papermind.schemas.tools import (
    LookupPaperArgs,
    LookupPaperResult,
    PaperGraphResult,
    PaperGraphServerArgs,
)
from papermind.scholar.client import ScholarAPI, ScholarClient
from papermind.scholar.errors import ScholarError, ScholarNotFound, ScholarRateLimited, ScholarUnavailable

SERVER_NAME = "papermind-semantic-scholar"
DEFAULT_HTTP_PORT = 8103

log = logging.getLogger(__name__)

# Tool parameters reuse the schema's own FieldInfo (constraints, descriptions,
# defaults), so the advertised input schema cannot drift from the shared models.
# tests/unit/test_scholar_server.py asserts the two schemas are identical.
_LOOKUP = LookupPaperArgs.model_fields
_GRAPH = PaperGraphServerArgs.model_fields

RATE_LIMITED_MESSAGE = "Semantic Scholar is rate-limiting us; answer from the paper alone."
UNAVAILABLE_MESSAGE = "Semantic Scholar is unavailable right now; answer from the paper alone."
INTERNAL_ERROR_MESSAGE = "The Semantic Scholar lookup failed unexpectedly; answer from the paper alone."
NO_PAPER_MESSAGE = (
    "No paper given and no paper is open. Pass `paper` as a title, an arXiv id, or a Semantic Scholar id."
)

T = TypeVar("T")
GraphKind = Literal["references", "citations"]


def agent_message(exc: ScholarError) -> str:
    """Plain-language tool error text: what went wrong and what the agent should do instead."""
    if isinstance(exc, ScholarRateLimited):
        return RATE_LIMITED_MESSAGE
    if isinstance(exc, ScholarUnavailable):
        return UNAVAILABLE_MESSAGE
    if isinstance(exc, ScholarNotFound):
        return f"{exc}. Try the exact title or an arXiv id, or answer from the paper alone."
    return f"Semantic Scholar could not answer ({exc}); answer from the paper alone."


async def _guarded(call: Awaitable[T], tool: str) -> T:
    try:
        return await call
    except ScholarError as exc:
        raise ToolError(agent_message(exc)) from exc
    except Exception as exc:
        # The SDK would report a crash to the model as a bare "Error executing
        # tool", which reads as "try again". Say instead that retrying will not
        # help, and keep the traceback in our logs.
        log.exception("%s crashed", tool)
        raise ToolError(INTERNAL_ERROR_MESSAGE) from exc


class _ClientSlot:
    """The client the tools use: injected (tests) or opened by the server lifespan."""

    def __init__(self, client: ScholarAPI | None) -> None:
        self.client = client

    def get(self) -> ScholarAPI:
        if self.client is None:
            raise ToolError("The Semantic Scholar client is not running; answer from the paper alone.")
        return self.client


def create_server(client: ScholarAPI | None = None, *, settings: Settings | None = None) -> MCPServer:
    """Build the server. With ``client`` given the caller owns its lifecycle;
    otherwise the lifespan opens one (Redis cache, or files if Redis is down) and closes it."""
    settings = settings or get_settings()
    slot = _ClientSlot(client)
    owns_client = client is None

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        # Entered once per process for stdio and streamable HTTP alike, so the
        # cache connection, HTTP pool, and single-flight table are shared by
        # every session.
        if owns_client:
            opened = await ScholarClient.create(settings)
            slot.client = opened
            log.info(
                "semantic_scholar ready (%s)", "API key" if opened.authenticated else "keyless shared pool"
            )
        try:
            yield
        finally:
            if owns_client and slot.client is not None:
                await slot.client.aclose()
                slot.client = None

    server = MCPServer(
        SERVER_NAME,
        instructions=(
            "Semantic Scholar for PaperMind: metadata for a cited paper, what a paper cites, and what cites it. "
            "Results are cached; failures are tool errors that say what to do instead."
        ),
        lifespan=lifespan,
    )

    @server.tool(
        description=(
            "Look up a paper that is not the one being read (a cited method, a dataset, a metric) "
            "by title, arXiv id, or DOI. An exact title returns just that paper."
        )
    )
    async def lookup_paper(
        query: Annotated[str, _LOOKUP["query"]],
        limit: Annotated[int, _LOOKUP["limit"]] = 3,
    ) -> LookupPaperResult:
        matches = await _guarded(slot.get().lookup(query, limit), "lookup_paper")
        return LookupPaperResult(query=query, matches=matches)

    async def graph(
        paper: str | None, default_paper: str | None, limit: int, kind: GraphKind
    ) -> PaperGraphResult:
        # Blank counts as omitted: the model sometimes sends "" for "the open paper".
        target = (paper or "").strip() or (default_paper or "").strip()
        if not target:
            raise ToolError(NO_PAPER_MESSAGE)
        api = slot.get()
        fetch = api.references if kind == "references" else api.citations
        return await _guarded(fetch(target, limit), f"get_{kind}")

    @server.tool(
        description="Papers that a paper cites (its bibliography), most-cited first. Omit `paper` for the open paper."
    )
    async def get_references(
        paper: Annotated[str | None, _GRAPH["paper"]] = None,
        limit: Annotated[int, _GRAPH["limit"]] = 10,
        default_paper: Annotated[str | None, _GRAPH["default_paper"]] = None,
    ) -> PaperGraphResult:
        return await graph(paper, default_paper, limit, "references")

    @server.tool(
        description=(
            "Papers that cite a paper: the most-cited among its most recent citations. "
            "Omit `paper` for the open paper."
        )
    )
    async def get_citations(
        paper: Annotated[str | None, _GRAPH["paper"]] = None,
        limit: Annotated[int, _GRAPH["limit"]] = 10,
        default_paper: Annotated[str | None, _GRAPH["default_paper"]] = None,
    ) -> PaperGraphResult:
        return await graph(paper, default_paper, limit, "citations")

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"HTTP port (default: from MCP_SCHOLAR_URL, else {DEFAULT_HTTP_PORT})",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    # stdout is the protocol channel under stdio; logs must go to stderr.
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request at INFO, which would drown the retry and fallback lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    server = create_server(settings=settings)
    if args.http:
        # Default to where the API's MCP client will look for us.
        port = args.port or urlsplit(settings.mcp_scholar_url).port or DEFAULT_HTTP_PORT
        server.run("streamable-http", host=args.host, port=port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
