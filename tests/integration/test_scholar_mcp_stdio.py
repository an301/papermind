"""Spawn the Semantic Scholar MCP server over stdio and call it like the API will.

Two runs: the normal configuration (shared Redis cache, so a warm cache may
answer without touching the network), and one with Redis unreachable and an
empty data dir, which must fall back to the file cache and fetch live.
"""

import sys
from pathlib import Path

import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp_types import CallToolResult, TextContent

from mcp_servers.semantic_scholar.server import RATE_LIMITED_MESSAGE
from papermind.config import REPO_ROOT
from papermind.schemas.tools import LookupPaperResult, PaperGraphResult

pytestmark = pytest.mark.integration


def text_of(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def server_params(**env: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_servers.semantic_scholar.server"],
        cwd=REPO_ROOT,
        env={"LOG_LEVEL": "WARNING", **env},
    )


def skip_if_throttled(result: CallToolResult) -> None:
    if result.is_error and RATE_LIMITED_MESSAGE in text_of(result):
        pytest.skip("Semantic Scholar's shared pool is rate-limiting; the transport itself worked")


async def test_lookup_paper_over_stdio() -> None:
    async with Client(server_params(), read_timeout_seconds=60) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools == {"lookup_paper", "get_references", "get_citations"}

        missing = await client.call_tool("get_references", {})
        assert missing.is_error
        assert "No paper given" in text_of(missing)

        result = await client.call_tool("lookup_paper", {"query": "Attention Is All You Need", "limit": 3})
        skip_if_throttled(result)
        assert not result.is_error, result.content
        parsed = LookupPaperResult.model_validate(result.structured_content)
        assert parsed.matches and parsed.matches[0].arxiv_id == "1706.03762"


async def test_stdio_falls_back_to_the_file_cache_without_redis(tmp_path: Path) -> None:
    params = server_params(REDIS_URL="redis://127.0.0.1:1/0", DATA_DIR=str(tmp_path))
    async with Client(params, read_timeout_seconds=60) as client:
        result = await client.call_tool("get_references", {"default_paper": "1706.03762", "limit": 5})
        skip_if_throttled(result)
        assert not result.is_error, result.content
        refs = PaperGraphResult.model_validate(result.structured_content)
        assert refs.paper == "1706.03762"
        assert 1 <= len(refs.papers) <= 5
        assert all(p.s2_id for p in refs.papers)

    cached = list((tmp_path / "cache" / "s2").glob("*.json"))
    assert cached, "the references page should have been written to the file cache"
