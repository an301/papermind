"""The retriever MCP server, driven in-process through the MCP client with a fake embedder."""

from pathlib import Path

import pytest
from mcp import Client

from mcp_servers.retriever.server import SERVER_NAME, create_server
from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.store import PaperStore
from papermind.retrieval.hybrid import HybridRetriever
from papermind.retrieval.index import IndexRegistry
from papermind.schemas.tools import SearchPaperArgs, SearchPaperResult, SearchPaperServerArgs
from tests.unit.conftest import TRANSFORMER_LIKE, HashEmbedder, make_record

PAPER = "1706.03762"


@pytest.fixture
def embedder() -> HashEmbedder:
    return HashEmbedder(dim=1024)


@pytest.fixture
def server(tmp_path: Path, embedder: HashEmbedder):
    # Not a yield fixture around ``Client``: the client's anyio task group must
    # be entered and exited in the same task.
    store = PaperStore(tmp_path / "papers")
    store.save(make_record(PAPER, TRANSFORMER_LIKE, chunker_version=CHUNKER_VERSION), b"%PDF-1.4\n")
    return create_server(HybridRetriever(IndexRegistry(store, embedder)))


async def search(client: Client, **args) -> SearchPaperResult:
    result = await client.call_tool("search_paper", args)
    assert not result.is_error, result.content
    return SearchPaperResult.model_validate(result.structured_content)


def test_server_name(server):
    assert server.name == SERVER_NAME == "papermind-retriever"


async def test_parameters_are_exactly_search_paper_server_args(server):
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {"search_paper"}
        schema = tools["search_paper"].input_schema
        expected = SearchPaperServerArgs.model_json_schema()
        assert schema["properties"] == expected["properties"]
        assert sorted(schema.get("required", [])) == sorted(expected.get("required", []))
        # Every model-facing field is there, with its constraints and description.
        assert set(SearchPaperArgs.model_fields) <= set(schema["properties"])
        assert schema["properties"]["k"]["maximum"] == 10
        output = tools["search_paper"].output_schema
        assert output is not None and set(output["properties"]) == set(SearchPaperResult.model_fields)


async def test_search_returns_structured_results(server, embedder):
    async with Client(server) as client:
        assert embedder.query_calls >= 1  # warmed in the lifespan, before any request
        result = await search(client, paper_id=PAPER, query="square root of d_k", k=3)
        assert result.paper_id == PAPER and result.mode == "hybrid" and result.query == "square root of d_k"
        assert len(result.chunks) == 3
        assert result.chunks[0].section_number == "3.2.1"
        assert result.chunks[0].bm25_rank == 1


async def test_modes_and_section_filter(server):
    async with Client(server) as client:
        bm25 = await search(client, paper_id=PAPER, query="P100 GPUs", mode="bm25")
        assert [c.section_number for c in bm25.chunks] == ["5.2"]
        assert bm25.mode == "bm25" and bm25.chunks[0].dense_rank is None
        dense = await search(
            client, paper_id=PAPER, query="positional encodings token order", mode="dense", k=1
        )
        assert dense.chunks[0].section_number == "3.5" and dense.chunks[0].bm25_rank is None
        filtered = await search(client, paper_id=PAPER, query="attention", section_filter="3.2")
        assert {c.section_number for c in filtered.chunks} <= {"3.2.1", "3.2.2"}


async def test_unknown_paper_is_a_clear_tool_error(server):
    async with Client(server) as client:
        result = await client.call_tool("search_paper", {"paper_id": "9999.99999", "query": "attention"})
        assert result.is_error
        message = " ".join(getattr(block, "text", "") for block in result.content)
        assert "Unknown paper_id '9999.99999'" in message


@pytest.mark.parametrize(
    "args",
    [
        {"paper_id": PAPER, "query": "attention", "k": 0},
        {"paper_id": PAPER, "query": "attention", "k": 11},
        {"paper_id": PAPER, "query": "a"},
        {"paper_id": PAPER, "query": "attention", "mode": "tfidf"},
        {"query": "attention"},
    ],
)
async def test_invalid_arguments_are_rejected(server, args):
    async with Client(server) as client:
        result = await client.call_tool("search_paper", args)
        assert result.is_error
