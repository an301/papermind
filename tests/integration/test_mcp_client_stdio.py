"""MCPManager against the three real servers, spawned over stdio as the app does.

Needs the ingested benchmark paper (data/papers/1706.03762) and the embedding
model; Neo4j is optional (the prereq server falls back to the in-memory graph).
Semantic Scholar calls are not made here, so no network is needed.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import AsyncIterator

import pytest

from papermind.agent.registry import build_registry
from papermind.config import get_settings
from papermind.mcp_client.manager import SERVER_NAMES, MCPManager, ToolExecutionError
from papermind.schemas.tools import CheckPrerequisitesResult, SearchPaperResult

pytestmark = pytest.mark.integration

PAPER_ID = "1706.03762"


@pytest.fixture
async def mcp() -> AsyncIterator[MCPManager]:
    settings = get_settings().model_copy(update={"mcp_transport": "stdio"})
    if not (settings.papers_dir / PAPER_ID / "paper.json").exists():
        pytest.skip(f"{PAPER_ID} is not ingested")
    manager = MCPManager(settings)
    await manager.start(required=SERVER_NAMES)
    try:
        yield manager
    finally:
        await manager.aclose()


async def test_all_servers_list_the_routed_tools(mcp: MCPManager) -> None:
    assert mcp.tool_names("retriever") >= {"search_paper"}
    assert mcp.tool_names("prereq_graph") >= {
        "check_prerequisites",
        "get_learning_path",
        "mark_known",
        "forget_concept",
    }
    assert mcp.tool_names("semantic_scholar") >= {"lookup_paper", "get_references", "get_citations"}
    # Every routed tool exists and accepts what the dispatcher sends.
    assert build_registry().verify_against(mcp) == []
    health = await mcp.health()
    assert all(check.ok for check in health.values()), health


async def test_search_and_prerequisites_return_contract_results(mcp: MCPManager) -> None:
    raw = await mcp.call(
        "retriever",
        "search_paper",
        {"query": "scaled dot-product attention", "paper_id": PAPER_ID, "mode": "hybrid"},
    )
    result = SearchPaperResult.model_validate(raw)
    assert result.chunks and result.chunks[0].chunk_id.startswith(f"{PAPER_ID}:sec_")
    assert any("3.2.1" in (c.section_number or "") for c in result.chunks)

    raw = await mcp.call(
        "prereq_graph", "check_prerequisites", {"concept": "attention", "user_id": "u_it_mcp"}
    )
    CheckPrerequisitesResult.model_validate(raw)


async def test_concurrent_calls_overlap(mcp: MCPManager) -> None:
    # Warm the retriever's index first so the timing measures dispatch, not the first build.
    await mcp.call("retriever", "search_paper", {"query": "warm up", "paper_id": PAPER_ID})
    queries = ["positional encoding", "label smoothing", "multi-head attention", "residual dropout"]

    async def timed(parallel: bool) -> float:
        started = time.perf_counter()
        calls = [
            mcp.call("retriever", "search_paper", {"query": q, "paper_id": PAPER_ID}) for q in queries
        ] + [mcp.call("prereq_graph", "check_prerequisites", {"concept": "softmax", "user_id": "u_it_mcp"})]
        if parallel:
            await asyncio.gather(*calls)
        else:
            for c in calls:
                await c
        return time.perf_counter() - started

    sequential, parallel = await timed(False), await timed(True)
    print(f"5 calls: sequential {sequential * 1000:.0f} ms, concurrent {parallel * 1000:.0f} ms")
    assert parallel <= sequential * 1.1


async def test_a_killed_server_is_restarted_on_the_next_call(mcp: MCPManager) -> None:
    pgrep = await asyncio.create_subprocess_exec(
        "pgrep",
        "-P",
        str(os.getpid()),
        "-f",
        "mcp_servers.prereq_graph.server",
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, _ = await pgrep.communicate()
    pids = [int(p) for p in stdout.split()]
    if not pids:
        pytest.skip("could not find the prereq_graph child process")
    for pid in pids:
        os.kill(pid, signal.SIGKILL)
    await asyncio.sleep(0.5)
    raw = await mcp.call("prereq_graph", "check_prerequisites", {"concept": "softmax", "user_id": "u_it_mcp"})
    assert CheckPrerequisitesResult.model_validate(raw).query == "softmax"


async def test_tool_errors_carry_the_server_message(mcp: MCPManager) -> None:
    with pytest.raises(ToolExecutionError) as info:
        await mcp.call("retriever", "search_paper", {"query": "attention", "paper_id": "no-such-paper"})
    assert info.value.kind == "tool_error"
    assert info.value.message
