"""The prereq_graph server as a real subprocess over stdio.

Two runs: one with Neo4j made unreachable, so the server must fall back to the
in-memory store over fixture files; one against the local Neo4j with a
namespaced fixture seeded first (and deleted afterwards).
"""

import sys
import uuid
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from papermind.config import get_settings
from papermind.graph.seed import seed_neo4j
from papermind.graph.store import create_driver
from papermind.graph.testing import sample_catalog, write_sample_files
from papermind.schemas.tools import CheckPrerequisitesResult, LearningPathResult, MarkKnownResult

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]


def server_params(**env: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_servers.prereq_graph.server"],
        cwd=REPO,
        env={"LOG_LEVEL": "WARNING", **env},
    )


async def exercise(client: Client, user: str, prefix: str = "") -> None:
    def cid(name: str) -> str:
        return f"{prefix}-{name}" if prefix else name

    def text(name: str) -> str:
        return f"{prefix} {name}" if prefix else name

    tools = {t.name for t in (await client.list_tools()).tools}
    assert tools == {"check_prerequisites", "get_learning_path", "mark_known", "forget_concept"}

    for concept in ("dot product", "exponentials"):
        result = await client.call_tool(
            "mark_known",
            {"user_id": user, "concept": text(concept), "confidence": 0.9, "source": "onboarding"},
        )
        assert not result.is_error, result.content
        assert MarkKnownResult.model_validate(result.structured_content).status == "added"

    result = await client.call_tool("check_prerequisites", {"user_id": user, "concept": text("Transformers")})
    check = CheckPrerequisitesResult.model_validate(result.structured_content)
    assert [g.concept_id for g in check.gaps] == [cid("transformer"), cid("attention"), cid("softmax")]
    assert check.altitude == "scaffolded"

    result = await client.call_tool("get_learning_path", {"user_id": user, "concept": cid("transformer")})
    path = LearningPathResult.model_validate(result.structured_content)
    assert [s.concept_id for s in path.steps] == [cid("softmax"), cid("attention"), cid("transformer")]

    result = await client.call_tool("forget_concept", {"user_id": user, "concept": cid("dot-product")})
    assert MarkKnownResult.model_validate(result.structured_content).status == "removed"

    result = await client.call_tool("check_prerequisites", {"user_id": user, "concept": "x"})
    assert result.is_error


async def test_stdio_with_in_memory_fallback(tmp_path):
    write_sample_files(tmp_path / "concepts")
    params = server_params(DATA_DIR=str(tmp_path), NEO4J_URI="bolt://127.0.0.1:1")
    async with Client(params, read_timeout_seconds=30) as client:
        await exercise(client, user="stdio-user")


async def test_stdio_against_neo4j():
    ns = f"pmtest-{uuid.uuid4().hex[:8]}"
    driver = create_driver(get_settings())
    try:
        await seed_neo4j(driver, sample_catalog(ns), retire_missing=False)
        # The server builds its resolution index at startup, after the seed.
        async with Client(server_params(), read_timeout_seconds=30) as client:
            await exercise(client, user=f"{ns}-user", prefix=ns)
        records, _, _ = await driver.execute_query(
            "MATCH (:User {id: $u})-[k:KNOWS]->(c) RETURN collect(c.id) AS ids", {"u": f"{ns}-user"}
        )
        assert records[0]["ids"] == [f"{ns}-exponential-function"]  # persisted by the subprocess
    finally:
        await driver.execute_query(
            "MATCH (n) WHERE (n:Concept OR n:Area OR n:User) AND n.id STARTS WITH $prefix DETACH DELETE n",
            {"prefix": f"{ns}-"},
        )
        await driver.close()
