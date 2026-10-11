"""The prereq_graph MCP server, driven in-process through the MCP client."""

import pytest
from mcp import Client

from mcp_servers.prereq_graph.server import SERVER_NAME, create_server
from papermind.graph.store import InMemoryGraphStore
from papermind.graph.testing import sample_catalog
from papermind.schemas.tools import (
    CheckPrerequisitesResult,
    CheckPrerequisitesServerArgs,
    ForgetConceptServerArgs,
    LearningPathResult,
    LearningPathServerArgs,
    MarkKnownResult,
    MarkKnownServerArgs,
)

TOOLS = {
    "check_prerequisites": (CheckPrerequisitesServerArgs, CheckPrerequisitesResult),
    "get_learning_path": (LearningPathServerArgs, LearningPathResult),
    "mark_known": (MarkKnownServerArgs, MarkKnownResult),
    "forget_concept": (ForgetConceptServerArgs, MarkKnownResult),
}


@pytest.fixture
def server():
    # Not a yield fixture around ``Client``: the client's anyio task group must
    # be entered and exited in the same task, and pytest-asyncio runs fixture
    # setup and teardown in different ones.
    return create_server(InMemoryGraphStore(sample_catalog()))


async def call(client, tool: str, **args):
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    return TOOLS[tool][1].model_validate(result.structured_content)


def test_server_name():
    assert create_server(InMemoryGraphStore(sample_catalog())).name == SERVER_NAME


async def test_tool_parameters_are_exactly_the_server_args(server):
    async with Client(server) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(listed) == set(TOOLS)
        for name, (args_model, result_model) in TOOLS.items():
            expected = args_model.model_json_schema()
            schema = listed[name].input_schema
            assert schema["properties"] == expected["properties"], name
            assert sorted(schema.get("required", [])) == sorted(expected.get("required", [])), name
            assert listed[name].output_schema is not None, name
            assert set(listed[name].output_schema["properties"]) == set(result_model.model_fields), name


async def test_check_and_learning_path_after_marking(server):
    async with Client(server) as client:
        for concept in ("Dot Product", "exponentials"):
            marked = await call(
                client, "mark_known", user_id="u1", concept=concept, confidence=0.9, source="onboarding"
            )
            assert marked.status == "added"

        check = await call(client, "check_prerequisites", user_id="u1", concept="Transformers")
        assert [g.concept_id for g in check.gaps] == ["transformer", "attention", "softmax"]
        assert [s.concept_id for s in check.known_stops] == ["dot-product", "exponential-function"]
        assert check.altitude == "scaffolded"

        path = await call(client, "get_learning_path", user_id="u1", concept="transformer")
        assert [s.concept_id for s in path.steps] == ["softmax", "attention", "transformer"]


async def test_unresolved_concept(server):
    async with Client(server) as client:
        check = await call(client, "check_prerequisites", user_id="u1", concept="attention head")
        assert check.matched is None and check.gaps == [] and check.altitude == "technical"
        assert check.suggestions


async def test_forget_concept_round_trip(server):
    async with Client(server) as client:
        await call(
            client,
            "mark_known",
            user_id="u1",
            concept="softmax",
            confidence=0.7,
            source="inferred",
            evidence="x",
        )
        forgotten = await call(client, "forget_concept", user_id="u1", concept="softmax")
        assert (forgotten.status, forgotten.confidence) == ("removed", 0.7)
        again = await call(client, "forget_concept", user_id="u1", concept="softmax")
        assert again.status == "not_found"


async def test_invalid_arguments_come_back_as_tool_errors(server):
    async with Client(server) as client:
        result = await client.call_tool(
            "mark_known", {"user_id": "u1", "concept": "softmax", "confidence": 1.5, "source": "inferred"}
        )
        assert result.is_error
        assert "confidence" in result.content[0].text

        result = await client.call_tool("check_prerequisites", {"user_id": "u1", "concept": "x"})
        assert result.is_error  # min_length=2 from CheckPrerequisitesArgs

        result = await client.call_tool(
            "mark_known", {"user_id": "u1", "concept": "softmax", "confidence": 0.5, "source": "guess"}
        )
        assert result.is_error
