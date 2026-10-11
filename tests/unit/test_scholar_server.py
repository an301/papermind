"""The MCP server over the real protocol (in-process transport), with a fake client."""

from typing import Any

import pytest
from mcp.client.client import Client
from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel

from mcp_servers.semantic_scholar import server as server_module
from mcp_servers.semantic_scholar.server import (
    INTERNAL_ERROR_MESSAGE,
    NO_PAPER_MESSAGE,
    RATE_LIMITED_MESSAGE,
    SERVER_NAME,
    UNAVAILABLE_MESSAGE,
    create_server,
)
from papermind.config import Settings
from papermind.schemas.tools import (
    LookupPaperArgs,
    LookupPaperResult,
    PaperGraphResult,
    PaperGraphServerArgs,
    ScholarPaper,
)
from papermind.scholar.errors import ScholarError, ScholarNotFound, ScholarRateLimited, ScholarUnavailable


def text_of(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


# No .env: these tests must not depend on the developer's machine.
SETTINGS = Settings(_env_file=None)


class FakeScholar:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []
        self.fail: Exception | None = None
        self.authenticated = False
        self.closed = False

    async def lookup(self, query: str, limit: int = 3) -> list[ScholarPaper]:
        self.calls.append(("lookup", query, limit))
        if self.fail:
            raise self.fail
        return [ScholarPaper(s2_id="s2", title=query, year=2014, authors=["D. Bahdanau"])]

    async def references(self, paper: str, limit: int = 10) -> PaperGraphResult:
        return self._graph("references", paper, limit)

    async def citations(self, paper: str, limit: int = 10) -> PaperGraphResult:
        return self._graph("citations", paper, limit)

    def _graph(self, kind: str, paper: str, limit: int) -> PaperGraphResult:
        self.calls.append((kind, paper, limit))
        if self.fail:
            raise self.fail
        return PaperGraphResult(
            paper=paper,
            resolved=ScholarPaper(s2_id="root", title="Root"),
            papers=[ScholarPaper(s2_id=f"p{i}", title=f"P{i}") for i in range(limit)],
            total=99,
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake() -> FakeScholar:
    return FakeScholar()


@pytest.fixture
def client(fake: FakeScholar) -> Client:
    # Entered inside each test: anyio cancel scopes cannot span an async
    # fixture's setup and teardown, which run in different tasks.
    return Client(create_server(fake, settings=SETTINGS))


def _schema_core(schema: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    props = {
        name: {k: v for k, v in prop.items() if k != "title"} for name, prop in schema["properties"].items()
    }
    return props, set(schema.get("required", []))


@pytest.mark.parametrize(
    ("tool", "model"),
    [
        ("lookup_paper", LookupPaperArgs),
        ("get_references", PaperGraphServerArgs),
        ("get_citations", PaperGraphServerArgs),
    ],
)
async def test_tool_input_schema_is_the_shared_contract(
    client: Client, tool: str, model: type[BaseModel]
) -> None:
    async with client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert _schema_core(tools[tool].input_schema) == _schema_core(model.model_json_schema())


async def test_server_identity_and_tools(client: Client) -> None:
    assert create_server(FakeScholar(), settings=SETTINGS).name == SERVER_NAME == "papermind-semantic-scholar"
    async with client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"lookup_paper", "get_references", "get_citations"}
    assert tools["lookup_paper"].output_schema is not None


async def test_lookup_returns_structured_result(client: Client, fake: FakeScholar) -> None:
    async with client:
        result = await client.call_tool("lookup_paper", {"query": "Neural Machine Translation", "limit": 2})
    assert not result.is_error
    parsed = LookupPaperResult.model_validate(result.structured_content)
    assert parsed.query == "Neural Machine Translation"
    assert parsed.matches[0].authors == ["D. Bahdanau"]
    assert fake.calls == [("lookup", "Neural Machine Translation", 2)]


async def test_lookup_rejects_out_of_contract_arguments(client: Client, fake: FakeScholar) -> None:
    async with client:
        too_short = await client.call_tool("lookup_paper", {"query": "x"})
        too_many = await client.call_tool("lookup_paper", {"query": "valid", "limit": 9})
    assert too_short.is_error
    assert too_many.is_error
    assert fake.calls == []


@pytest.mark.parametrize(("tool", "kind"), [("get_references", "references"), ("get_citations", "citations")])
async def test_graph_tools_fall_back_to_the_open_paper(
    client: Client, fake: FakeScholar, tool: str, kind: str
) -> None:
    async with client:
        result = await client.call_tool(tool, {"default_paper": "1706.03762", "limit": 3})
    assert not result.is_error
    parsed = PaperGraphResult.model_validate(result.structured_content)
    assert parsed.paper == "1706.03762"
    assert len(parsed.papers) == 3
    assert parsed.total == 99
    assert fake.calls == [(kind, "1706.03762", 3)]


async def test_explicit_paper_wins_over_the_open_one(client: Client, fake: FakeScholar) -> None:
    async with client:
        await client.call_tool("get_references", {"paper": "1409.0473", "default_paper": "1706.03762"})
        await client.call_tool("get_references", {"paper": "  ", "default_paper": "1706.03762"})
    assert fake.calls == [("references", "1409.0473", 10), ("references", "1706.03762", 10)]


async def test_no_paper_at_all_is_a_tool_error(client: Client, fake: FakeScholar) -> None:
    async with client:
        result = await client.call_tool("get_citations", {})
    assert result.is_error
    assert NO_PAPER_MESSAGE in text_of(result)
    assert fake.calls == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ScholarRateLimited("429"), RATE_LIMITED_MESSAGE),
        (ScholarUnavailable("503"), UNAVAILABLE_MESSAGE),
        (
            ScholarNotFound("Semantic Scholar has no paper matching 'Foo'"),
            "Try the exact title or an arXiv id",
        ),
        (ScholarError("HTTP 400: bad fields"), "answer from the paper alone"),
    ],
)
async def test_failures_become_actionable_tool_errors(
    client: Client, fake: FakeScholar, error: ScholarError, expected: str
) -> None:
    fake.fail = error
    async with client:
        results = [
            await client.call_tool("lookup_paper", {"query": "Foo"}),
            await client.call_tool("get_references", {"paper": "Foo"}),
        ]
    for result in results:
        assert result.is_error
        text = text_of(result)
        assert expected in text
        assert "Traceback" not in text


async def test_unexpected_crashes_say_retrying_will_not_help(client: Client, fake: FakeScholar) -> None:
    fake.fail = KeyError("paperId")
    async with client:
        result = await client.call_tool("get_citations", {"paper": "1706.03762"})
    assert result.is_error
    assert INTERNAL_ERROR_MESSAGE in text_of(result)
    assert "KeyError" not in text_of(result)


async def test_lifespan_opens_and_closes_its_own_client(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[FakeScholar] = []

    async def fake_create(settings: Settings | None = None) -> FakeScholar:
        opened.append(FakeScholar())
        return opened[-1]

    monkeypatch.setattr(server_module.ScholarClient, "create", staticmethod(fake_create))
    async with Client(create_server(settings=SETTINGS)) as client:
        result = await client.call_tool("lookup_paper", {"query": "Attention Is All You Need"})
        assert not result.is_error
        assert len(opened) == 1 and not opened[0].closed
    assert opened[0].closed
    assert opened[0].calls == [("lookup", "Attention Is All You Need", 3)]
