from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from papermind.graph.store import InMemoryGraphStore
from papermind.graph.testing import sample_catalog
from tests.unit.test_api_support import FakeMCP, make_harness, make_settings


class _Driver:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def verify_connectivity(self) -> None:
        if self.error is not None:
            raise self.error


class _Neo4jLike(InMemoryGraphStore):
    """The in-memory store dressed as the Neo4j one, as far as the health check looks."""

    backend = "neo4j"  # type: ignore[assignment]

    def __init__(self, driver: _Driver) -> None:
        super().__init__(sample_catalog())
        self.driver = driver


def _harness(tmp_path: Path, *, key: bool = True, driver: _Driver | None = None, mcp: Any = None):
    settings = make_settings(tmp_path, anthropic_api_key="sk-test" if key else None)
    graph = _Neo4jLike(driver or _Driver())
    return make_harness(tmp_path, settings=settings, graph=graph, mcp=mcp if mcp is not None else FakeMCP())


async def test_everything_up_is_ok(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    async with h.client() as client:
        body = (await client.get("/api/health")).json()
    assert body["status"] == "ok"
    assert body["version"] == h.res.settings.app_version
    assert set(body["checks"]) == {
        "redis",
        "neo4j",
        "mcp:retriever",
        "mcp:prereq_graph",
        "mcp:semantic_scholar",
        "anthropic_key",
    }
    assert all(check["ok"] for check in body["checks"].values())


async def test_each_missing_piece_degrades_with_a_reason(tmp_path: Path) -> None:
    h = _harness(
        tmp_path,
        key=False,
        driver=_Driver(OSError("connection refused")),
        mcp=FakeMCP(down={"semantic_scholar"}),
    )
    h.res.redis = None
    async with h.client() as client:
        response = await client.get("/api/health")
    assert response.status_code == 200, "degraded is reported, not raised"
    body = response.json()
    checks = body["checks"]
    assert body["status"] == "degraded"
    assert checks["redis"]["ok"] is False and "memory" in checks["redis"]["detail"]
    assert checks["neo4j"] == {"ok": False, "detail": "OSError: connection refused"}
    assert checks["mcp:semantic_scholar"]["ok"] is False
    assert checks["mcp:retriever"]["ok"] is True
    assert checks["anthropic_key"]["ok"] is False and "ANTHROPIC_API_KEY" in checks["anthropic_key"]["detail"]


async def test_in_memory_graph_is_reported(tmp_path: Path) -> None:
    h = make_harness(tmp_path, mcp=FakeMCP())
    async with h.client() as client:
        checks = (await client.get("/api/health")).json()["checks"]
    assert checks["neo4j"]["ok"] is False and "in this process's memory" in checks["neo4j"]["detail"]


async def test_no_mcp_manager_marks_every_server_down(tmp_path: Path) -> None:
    h = make_harness(tmp_path, mcp=None)
    async with h.client() as client:
        checks = (await client.get("/api/health")).json()["checks"]
    assert all(not checks[f"mcp:{s}"]["ok"] for s in ("retriever", "prereq_graph", "semantic_scholar"))


async def test_a_hung_dependency_cannot_hang_the_endpoint(tmp_path: Path, monkeypatch) -> None:
    import papermind.api.routes.health as health

    monkeypatch.setattr(health, "CHECK_TIMEOUT_S", 0.05)

    class HungDriver(_Driver):
        async def verify_connectivity(self) -> None:
            await asyncio.sleep(30)

    h = _harness(tmp_path, driver=HungDriver())
    async with h.client() as client:
        body = await asyncio.wait_for(client.get("/api/health"), 2)
    assert body.json()["checks"]["neo4j"]["ok"] is False
    assert "no answer" in body.json()["checks"]["neo4j"]["detail"]
