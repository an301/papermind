"""The app factory and the lifespan's degrade-or-fail decisions (no services: connections are faked)."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

import fakeredis
import pytest
from mcp.client._memory import InMemoryTransport
from redis.exceptions import ConnectionError as RedisConnectionError

import papermind.main as main
from mcp_servers.prereq_graph.server import create_server as create_prereq_server
from papermind.graph.store import GraphStore, InMemoryGraphStore
from papermind.graph.testing import sample_catalog
from papermind.mcp_client.manager import SERVER_NAMES, MCPManager
from papermind.memory.users import mark_self_reported
from tests.unit.test_api_support import make_settings


class _UnreachableRedis:
    closed = False

    async def ping(self) -> bool:
        raise RedisConnectionError("Error 61 connecting to localhost:6379. Connection refused.")

    async def aclose(self) -> None:
        type(self).closed = True


class _Redis:
    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass


@pytest.fixture
def unreachable(monkeypatch) -> type[_UnreachableRedis]:
    _UnreachableRedis.closed = False
    monkeypatch.setattr(main.Redis, "from_url", classmethod(lambda cls, *a, **k: _UnreachableRedis()))
    return _UnreachableRedis


async def test_dev_falls_back_to_memory_when_redis_is_down(tmp_path: Path, unreachable, caplog) -> None:
    settings = make_settings(tmp_path, environment="dev", redis_url="redis://:s3cret@localhost:6379/0")
    assert await main.connect_redis(settings) is None
    assert unreachable.closed, "the failed client is closed, not leaked"
    assert "unreachable" in caplog.text
    assert "s3cret" not in caplog.text


async def test_prod_refuses_to_start_without_redis(tmp_path: Path, unreachable) -> None:
    settings = make_settings(tmp_path, environment="prod")
    with pytest.raises(RuntimeError, match="Production needs it"):
        await main.connect_redis(settings)


async def test_a_reachable_redis_is_returned(tmp_path: Path, monkeypatch) -> None:
    client = _Redis()
    monkeypatch.setattr(main.Redis, "from_url", classmethod(lambda cls, *a, **k: client))
    assert await main.connect_redis(make_settings(tmp_path)) is client


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("redis://:pw@host:6379/0", "redis://***@host:6379/0"),
        ("redis://user:pw@host/1", "redis://***@host/1"),
        ("redis://localhost:6379/0", "redis://localhost:6379/0"),
        # Parsers end the userinfo at the last '@', so a password may contain one.
        ("redis://user:s3cr@t!@redis.internal:6379/0", "redis://***@redis.internal:6379/0"),
        ("rediss://default:Ab@cd@x.cache.amazonaws.com:6379", "rediss://***@x.cache.amazonaws.com:6379"),
        ("redis://:pa#ss@host/0", "redis://***@host/0"),
    ],
)
def test_redact_url(url: str, shown: str) -> None:
    assert main.redact_url(url) == shown


def test_create_app_mounts_every_route_under_api(tmp_path: Path) -> None:
    app = main.create_app(make_settings(tmp_path))
    paths = set(app.openapi()["paths"])
    expected = {
        "/api/health",
        "/api/papers",
        "/api/papers/upload",
        "/api/papers/{paper_id}",
        "/api/papers/{paper_id}/pdf",
        "/api/papers/{paper_id}/chunks/{chunk_id}",
        "/api/papers/{paper_id}/suggestions",
        "/api/users",
        "/api/users/{user_id}",
        "/api/users/{user_id}/knowledge",
        "/api/users/{user_id}/knowledge/areas",
        "/api/users/{user_id}/knowledge/{concept_id}",
        "/api/concepts",
        "/api/concepts/areas",
        "/api/concepts/{concept_id}",
        "/api/sessions",
        "/api/sessions/{session_id}",
        "/api/chat",
    }
    assert expected <= paths


# ---------------------------------------------------------------- one owner for the in-memory graph


def _never_spawn() -> AbstractAsyncContextManager[Any]:
    raise OSError("a separate prereq_graph process would hold a separate in-memory store")


def _ready(store: Any) -> asyncio.Future[GraphStore]:
    future: asyncio.Future[GraphStore] = asyncio.get_running_loop().create_future()
    future.set_result(store)
    return future


async def _gaps(mcp: MCPManager, concept: str) -> list[str]:
    result = await mcp.call(
        "prereq_graph", "check_prerequisites", {"user_id": "u_reader", "concept": concept}
    )
    return [gap["concept_id"] for gap in result["gaps"]]


async def test_without_neo4j_the_agent_and_the_api_share_one_knowledge_store(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    graph = InMemoryGraphStore(sample_catalog())
    transport = main.prereq_graph_transport(settings, _ready(graph), _never_spawn)

    async with MCPManager(settings, transports={"prereq_graph": transport}) as mcp:
        assert "softmax" in await _gaps(mcp, "attention")
        # The reader tells the API; the agent's tool sees it.
        await mark_self_reported(graph, "u_reader", "softmax")
        assert "softmax" not in await _gaps(mcp, "attention")
        # The mastery pass writes through MCP; the knowledge routes see it.
        await mcp.call(
            "prereq_graph",
            "mark_known",
            {"user_id": "u_reader", "concept": "dot-product", "confidence": 0.8, "source": "inferred"},
        )
        assert "dot-product" in await graph.known("u_reader")
        # Undo through the API reaches the agent too.
        await graph.forget("u_reader", "dot-product")
        assert "dot-product" in await _gaps(mcp, "attention")


async def test_with_neo4j_prereq_graph_is_its_own_server(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    class Neo4jBacked:
        backend = "neo4j"

    connects: list[str] = []

    def separate() -> AbstractAsyncContextManager[Any]:
        connects.append("separate")
        return InMemoryTransport(
            create_prereq_server(InMemoryGraphStore(sample_catalog()), settings=settings)
        )

    transport = main.prereq_graph_transport(settings, _ready(Neo4jBacked()), separate)
    async with MCPManager(settings, transports={"prereq_graph": transport}) as mcp:
        assert "softmax" in await _gaps(mcp, "attention")
    assert connects == ["separate"]


async def test_prod_refuses_to_start_without_neo4j(tmp_path: Path, monkeypatch) -> None:
    async def memory_graph(settings: Any) -> InMemoryGraphStore:
        return InMemoryGraphStore(sample_catalog())

    async def redis(settings: Any) -> Any:
        return fakeredis.FakeAsyncRedis(decode_responses=True)

    monkeypatch.setattr(main, "get_graph_store", memory_graph)
    monkeypatch.setattr(main, "connect_redis", redis)
    monkeypatch.setattr(
        main, "default_transports", lambda settings: dict.fromkeys(SERVER_NAMES, _never_spawn)
    )
    settings = make_settings(tmp_path, environment="prod", neo4j_uri="bolt://neo4j.internal:7687")

    with pytest.raises(RuntimeError, match=r"Neo4j at bolt://neo4j\.internal:7687 is unreachable"):
        async with main.open_resources(settings):
            pass
