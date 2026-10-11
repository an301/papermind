"""The real app with its stores unreachable: what still works, and that it stays consistent.

No services are needed (Redis and Neo4j are pointed at a closed local port);
these are integration tests because they use the real Neo4j driver against a
real socket and spawn the MCP servers over stdio.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import httpx
import pytest

from papermind.api.chat_turn import GRAPH_READ_TIMEOUT_S
from papermind.config import REPO_ROOT, Settings
from papermind.graph.store import Neo4jGraphStore, create_driver
from papermind.main import create_app
from tests.unit.test_api_support import PAPER_ID, FakeRunner, make_harness, make_settings, parse_sse

pytestmark = pytest.mark.integration

CLOSED = "bolt://localhost:1"


async def test_chat_is_answered_while_neo4j_is_down(tmp_path: Path) -> None:
    """The driver retries a failing transaction for 30 s; the turn must not wait for that."""
    settings = make_settings(tmp_path, neo4j_uri=CLOSED)
    graph = Neo4jGraphStore(create_driver(settings))  # connected once; the server has since gone away
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner, settings=settings)
    h.res.graph = graph
    session = await h.res.sessions.create("u_reader", PAPER_ID, "intermediate")
    try:
        async with h.client() as client:
            started = time.perf_counter()
            response = await client.post(
                "/api/chat", json={"session_id": session.session_id, "message": "How?"}
            )
            took = time.perf_counter() - started
    finally:
        await graph.aclose()

    assert response.status_code == 200
    assert [name for name, _ in parse_sse(response.text)][-1] == "done"
    assert runner.contexts and runner.contexts[0].knowledge is None
    assert took < 2 * GRAPH_READ_TIMEOUT_S + 2


def _degraded_settings(root: Path) -> Settings:
    data = root / "data"
    shutil.copytree(REPO_ROOT / "data" / "papers" / PAPER_ID, data / "papers" / PAPER_ID)
    shutil.copytree(REPO_ROOT / "data" / "concepts", data / "concepts")
    (data / "models").symlink_to(REPO_ROOT / "data" / "models")
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        data_dir=data,
        anthropic_api_key=None,
        environment="dev",
        prewarm_cache=False,
        redis_url="redis://localhost:1/0",
        neo4j_uri=CLOSED,
    )


async def test_without_neo4j_the_agent_tools_and_the_api_see_the_same_reader(tmp_path: Path) -> None:
    app = create_app(_degraded_settings(tmp_path))
    async with app.router.lifespan_context(app):
        res = app.state.resources
        assert res.graph.backend == "memory"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as client:
            user_id = (await client.post("/api/users")).json()["user_id"]

            async def gaps(concept: str) -> list[str]:
                result = await res.mcp.call(
                    "prereq_graph", "check_prerequisites", {"user_id": user_id, "concept": concept}
                )
                return [gap["concept_id"] for gap in result["gaps"]]

            assert "softmax" in await gaps("scaled dot-product attention")
            reported = await client.post(f"/api/users/{user_id}/knowledge", json={"concept": "softmax"})
            assert reported.status_code == 200
            assert "softmax" not in await gaps("scaled dot-product attention")

            await res.mcp.call(
                "prereq_graph",
                "mark_known",
                {
                    "user_id": user_id,
                    "concept": "attention-mechanism",
                    "confidence": 0.9,
                    "source": "inferred",
                },
            )
            known = (await client.get(f"/api/users/{user_id}/knowledge")).json()
            assert {k["concept_id"] for k in known} >= {"softmax", "attention-mechanism"}

            undone = await client.delete(f"/api/users/{user_id}/knowledge/attention-mechanism")
            assert undone.status_code == 204
            assert "attention-mechanism" in await gaps("attention mechanism")

            health = (await client.get("/api/health")).json()["checks"]
            assert health["mcp:prereq_graph"]["ok"] is True
            assert "prereq_graph runs in-process" in health["neo4j"]["detail"]
