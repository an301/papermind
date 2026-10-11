"""MCPManager against real MCP servers running in-process over memory streams.

No subprocesses: each "server" is an ``MCPServer`` driven through mcp's own
in-memory transport, so the client session, request multiplexing, error
results and timeouts are the real ones.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Any

import anyio
import pytest
from mcp.client._memory import InMemoryTransport
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, SecretStr

from papermind.mcp_client.manager import MCPManager, ToolExecutionError, child_env, settings_env
from tests.unit.fakes import fake_settings


class Echo(BaseModel):
    msg: str
    at: float


def make_server(name: str = "echo") -> MCPServer:
    server = MCPServer(name, log_level="WARNING")

    @server.tool(description="Echo after a delay.")
    async def echo(
        msg: Annotated[str, Field(description="text")], delay_s: Annotated[float, Field(ge=0)] = 0.0
    ) -> Echo:
        await anyio.sleep(delay_s)
        return Echo(msg=msg, at=time.monotonic())

    @server.tool(description="Always fails.")
    async def fail(reason: str) -> Echo:
        # ToolError is how the real servers word an error for the model; any
        # other exception reaches the client only as "Error executing tool".
        raise ToolError(f"cannot do that: {reason}")

    return server


class Spawner:
    """A transport factory that counts connections and can be told to fail."""

    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self.connects = 0
        self.broken = False

    def __call__(self) -> AbstractAsyncContextManager[Any]:
        self.connects += 1
        if self.broken:
            raise OSError("spawn failed")
        return InMemoryTransport(self.server)


def manager(
    transports: dict[str, Callable[[], AbstractAsyncContextManager[Any]]], **settings: Any
) -> MCPManager:
    return MCPManager(fake_settings(**settings), transports=transports)


async def test_call_returns_structured_content() -> None:
    async with manager({"a": Spawner(make_server())}) as mcp:
        result = await mcp.call("a", "echo", {"msg": "hi"})
        assert result["msg"] == "hi"
        assert mcp.tool_names("a") == {"echo", "fail"}
        assert set(mcp.tool_schemas("a")["echo"]["properties"]) == {"msg", "delay_s"}


async def test_concurrent_calls_on_one_session_overlap() -> None:
    async with manager({"a": Spawner(make_server()), "b": Spawner(make_server("b"))}) as mcp:
        started = time.perf_counter()
        results = await asyncio.gather(
            *(mcp.call("a", "echo", {"msg": str(i), "delay_s": 0.3}) for i in range(5)),
            mcp.call("b", "echo", {"msg": "other", "delay_s": 0.3}),
        )
        elapsed = time.perf_counter() - started
    assert [r["msg"] for r in results] == ["0", "1", "2", "3", "4", "other"]
    # Six 0.3s calls: serialised they would take 1.8s.
    assert elapsed < 0.9


async def test_server_error_becomes_a_tool_error_with_its_message() -> None:
    async with manager({"a": Spawner(make_server())}) as mcp:
        with pytest.raises(ToolExecutionError) as info:
            await mcp.call("a", "fail", {"reason": "bad input"})
    assert info.value.kind == "tool_error"
    assert "cannot do that: bad input" in info.value.message


async def test_invalid_arguments_are_a_tool_error() -> None:
    async with manager({"a": Spawner(make_server())}) as mcp:
        with pytest.raises(ToolExecutionError) as info:
            await mcp.call("a", "echo", {"msg": "x", "delay_s": -1})
    assert info.value.kind == "tool_error"


async def test_slow_call_times_out() -> None:
    async with manager({"a": Spawner(make_server())}, mcp_call_timeout_s=0.2) as mcp:
        started = time.perf_counter()
        with pytest.raises(ToolExecutionError) as info:
            await mcp.call("a", "echo", {"msg": "x", "delay_s": 5})
        assert time.perf_counter() - started < 2
        # The session survives a timed-out request.
        assert (await mcp.call("a", "echo", {"msg": "after"}))["msg"] == "after"
    assert info.value.kind == "timeout"


async def test_dead_server_is_restarted_once_on_the_next_call() -> None:
    spawner = Spawner(make_server())
    async with manager({"a": spawner}) as mcp:
        await mcp._conns["a"].stop()  # the child died
        assert (await mcp.call("a", "echo", {"msg": "back"}))["msg"] == "back"
    assert spawner.connects == 2


async def test_server_that_cannot_start_is_unavailable_but_others_work() -> None:
    broken = Spawner(make_server("broken"))
    broken.broken = True
    async with manager({"good": Spawner(make_server()), "broken": broken}) as mcp:
        assert (await mcp.call("good", "echo", {"msg": "ok"}))["msg"] == "ok"
        with pytest.raises(ToolExecutionError) as info:
            await mcp.call("broken", "echo", {"msg": "x"})
        assert info.value.kind == "unavailable"
        # Inside the cooldown no respawn is attempted on every call.
        assert broken.connects == 1
        health = await mcp.health()
    assert health["mcp:good"].ok is True
    assert health["mcp:broken"].ok is False


async def test_required_server_failing_to_start_raises() -> None:
    broken = Spawner(make_server())
    broken.broken = True
    mcp = manager({"a": broken})
    with pytest.raises(ToolExecutionError):
        await mcp.start(required=["a"])
    await mcp.aclose()


async def test_no_calls_after_close() -> None:
    spawner = Spawner(make_server())
    mcp = manager({"a": spawner})
    await mcp.start()
    await mcp.aclose()
    with pytest.raises(ToolExecutionError) as info:
        await mcp.call("a", "echo", {"msg": "x"})
    assert info.value.kind == "unavailable"
    assert spawner.connects == 1


async def test_unknown_server() -> None:
    async with manager({"a": Spawner(make_server())}) as mcp:
        with pytest.raises(ToolExecutionError):
            await mcp.call("nope", "echo", {})


def test_child_env_passes_the_environment_through_without_the_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TMPDIR", "/tmp/somewhere")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    settings = fake_settings(anthropic_api_key=SecretStr("sk-secret"), parallel_tools=False, retrieval_k=7)
    env = child_env(settings)
    assert env["TMPDIR"] == "/tmp/somewhere"
    assert "ANTHROPIC_API_KEY" not in env
    assert "sk-secret" not in env.values()
    # Resolved settings win over whatever the environment said.
    assert env["PARALLEL_TOOLS"] == "false"
    assert env["RETRIEVAL_K"] == "7"


def test_settings_env_round_trips_through_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    original = fake_settings(
        cors_origins=["http://a", "http://b"], prompt_caching=False, mcp_call_timeout_s=12.5
    )
    for key, value in settings_env(original).items():
        monkeypatch.setenv(key, value)
    restored = fake_settings()  # what a child server reads
    assert restored.cors_origins == original.cors_origins
    assert restored.prompt_caching is False
    assert restored.mcp_call_timeout_s == 12.5
    assert restored.data_dir == original.data_dir
