"""The retriever MCP server as a real subprocess, with the real embedding model.

Runs over stdio and over streamable HTTP against a throwaway data directory
holding one synthetic paper, then (when the benchmark papers have been
ingested) over stdio against the real data directory. The model files are
shared with the repo's data/models through a symlink, so nothing is
downloaded when the model is already cached; the first run downloads it.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from papermind.config import get_settings
from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.store import PaperStore
from papermind.schemas.tools import SearchPaperArgs, SearchPaperResult
from tests.unit.conftest import TRANSFORMER_LIKE, make_record

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
PAPER = "1706.03762"
# Loading the model and warming it happens before the server answers.
STARTUP_TIMEOUT_S = 180


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    store = PaperStore(tmp_path / "papers")
    store.save(make_record(PAPER, TRANSFORMER_LIKE, chunker_version=CHUNKER_VERSION), b"%PDF-1.4\n")
    models = get_settings().data_dir / "models"
    models.mkdir(parents=True, exist_ok=True)
    (tmp_path / "models").symlink_to(models, target_is_directory=True)
    return tmp_path


def stdio_params(data_dir: Path | None) -> StdioServerParameters:
    env = {"LOG_LEVEL": "WARNING"}
    if data_dir is not None:
        env["DATA_DIR"] = str(data_dir)
    return StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_servers.retriever.server"], cwd=REPO, env=env
    )


async def exercise(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"search_paper"}
    properties = tools["search_paper"].input_schema["properties"]
    assert set(SearchPaperArgs.model_fields) <= set(properties)
    assert {"paper_id", "mode"} <= set(properties)
    assert set(tools["search_paper"].input_schema["required"]) == {"paper_id", "query"}

    result = await client.call_tool(
        "search_paper", {"paper_id": PAPER, "query": "square root of d_k", "k": 3}
    )
    assert not result.is_error, result.content
    found = SearchPaperResult.model_validate(result.structured_content)
    assert found.mode == "hybrid" and len(found.chunks) == 3
    assert found.chunks[0].chunk_id == f"{PAPER}:sec_3.2.1_scaled_dot_product_attention"

    result = await client.call_tool(
        "search_paper",
        {"paper_id": PAPER, "query": "how long did training take on the GPUs", "mode": "dense", "k": 1},
    )
    dense = SearchPaperResult.model_validate(result.structured_content)
    assert dense.chunks[0].section_number == "5.2"

    result = await client.call_tool("search_paper", {"paper_id": "0000.00000", "query": "attention"})
    assert result.is_error
    assert "Unknown paper_id" in " ".join(getattr(block, "text", "") for block in result.content)


async def test_stdio(data_dir: Path) -> None:
    async with Client(stdio_params(data_dir), read_timeout_seconds=STARTUP_TIMEOUT_S) as client:
        await exercise(client)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def http_server(data_dir: Path) -> Iterator[str]:
    port = _free_port()
    env = {**os.environ, "DATA_DIR": str(data_dir), "LOG_LEVEL": "WARNING"}
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_servers.retriever.server", "--http", "--port", str(port)],
        cwd=REPO,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


async def _wait_until_listening(url: str) -> None:
    async with httpx.AsyncClient() as http:
        for _ in range(STARTUP_TIMEOUT_S * 4):
            try:
                await http.get(url, timeout=1.0)
                return
            except httpx.TransportError:
                await asyncio.sleep(0.25)
    raise AssertionError(f"retriever did not start listening at {url}")


async def test_streamable_http(http_server: str) -> None:
    await _wait_until_listening(http_server)
    async with Client(http_server, read_timeout_seconds=STARTUP_TIMEOUT_S) as client:
        await exercise(client)


BENCHMARK_QUESTIONS = [
    ("why divide by the square root of d_k", "3.2.1"),
    ("how much compute did training take", "5.2"),
]


@pytest.mark.skipif(
    not (get_settings().papers_dir / PAPER / "paper.json").exists(),
    reason="benchmark papers not ingested (python scripts/ingest_benchmark.py)",
)
async def test_stdio_against_the_ingested_benchmark_paper() -> None:
    async with Client(stdio_params(None), read_timeout_seconds=STARTUP_TIMEOUT_S) as client:
        for question, section in BENCHMARK_QUESTIONS:
            result = await client.call_tool("search_paper", {"paper_id": PAPER, "query": question})
            found = SearchPaperResult.model_validate(result.structured_content)
            assert section in [c.section_number for c in found.chunks], (question, found.chunks)
