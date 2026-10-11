"""Client side of the three MCP servers: retriever, prereq_graph, semantic_scholar.

The manager starts all three concurrently at app startup and keeps their
sessions open for the app's lifetime. Over stdio it spawns
``python -m mcp_servers.<name>.server`` itself; over HTTP it connects to
``settings.mcp_*_url``.

Each connection is owned by its own long-lived task. The transports are anyio
task groups, whose cancel scopes must be exited by the task that entered them;
a restart can be triggered from whichever request task noticed the dead
server, so no request task may own a connection. The owner task enters the
transport and session on an ``AsyncExitStack``, parks until asked to stop, and
exits them itself.

One session serves many concurrent ``call_tool`` requests (the protocol
multiplexes by request id), which is what makes parallel tool dispatch real.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any, Literal, Protocol

import anyio
from mcp import ClientSession, MCPError, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import CONNECTION_CLOSED, REQUEST_TIMEOUT
from pydantic import SecretStr

from papermind.config import REPO_ROOT, Settings, get_settings
from papermind.schemas.api import ServiceCheck

log = logging.getLogger(__name__)

SERVER_NAMES: tuple[str, ...] = ("retriever", "prereq_graph", "semantic_scholar")
# The retriever may load an embedding model before it answers `initialize`.
STARTUP_TIMEOUT_S = 90.0
SHUTDOWN_TIMEOUT_S = 10.0
PING_TIMEOUT_S = 3.0
# A server that just failed to start is not respawned on every call: each
# attempt can take the whole startup timeout.
RESTART_COOLDOWN_S = 15.0
# Never handed to the servers: none of them calls the model.
_CHILD_ENV_EXCLUDE = frozenset({"anthropic_api_key"})

Transport = Callable[[], AbstractAsyncContextManager[Any]]
ErrorKind = Literal["tool_error", "timeout", "unavailable"]


class ToolExecutionError(Exception):
    """A tool call that did not produce a result. ``message`` is safe to show the model."""

    def __init__(self, server: str, tool: str, message: str, *, kind: ErrorKind) -> None:
        super().__init__(f"{server}.{tool}: {message}")
        self.server = server
        self.tool = tool
        self.message = message
        self.kind: ErrorKind = kind


class MCPCaller(Protocol):
    """The part of the manager the agent uses; the unit-test fake implements it too."""

    async def call(self, server: str, tool: str, args: dict[str, Any]) -> dict[str, Any]: ...

    def tool_names(self, server: str) -> set[str]: ...

    def tool_schemas(self, server: str) -> dict[str, dict[str, Any]]: ...


class _ConnectionLost(Exception):
    pass


def settings_env(settings: Settings) -> dict[str, str]:
    """This process's resolved settings as environment variables for a child server.

    The servers read their configuration through ``get_settings()`` like
    everything else. Handing them our resolved values, which win over the
    inherited environment and over ``.env``, makes overrides applied
    in-process -- a test's ``data_dir``, an ablation's retrieval mode -- reach
    the children too.
    """
    env: dict[str, str] = {}
    for name in type(settings).model_fields:
        if name in _CHILD_ENV_EXCLUDE:
            continue
        value = getattr(settings, name)
        if value is None:
            continue
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, list | dict):
            text = json.dumps(value)
        else:
            text = str(value)
        env[name.upper()] = text
    return env


def child_env(settings: Settings) -> dict[str, str]:
    """The environment for a spawned server: this process's, with the resolved settings on top.

    mcp's stdio client passes only PATH, HOME and a few others unless told
    otherwise. The servers also need what any process needs that is not a
    PaperMind setting -- TMPDIR (the default embedding-model cache lives under
    it), HF_HOME, proxies, CA bundles -- so the environment is passed through
    whole. Nothing here is read as configuration; that stays in ``config.py``.
    """
    env = {key: value for key, value in os.environ.items() if key.lower() not in _CHILD_ENV_EXCLUDE}
    env.update(settings_env(settings))
    return env


def default_transports(settings: Settings) -> dict[str, Transport]:
    if settings.mcp_transport == "http":
        urls = {
            "retriever": settings.mcp_retriever_url,
            "prereq_graph": settings.mcp_prereq_url,
            "semantic_scholar": settings.mcp_scholar_url,
        }
        return {name: _http(url) for name, url in urls.items()}
    env = child_env(settings)
    return {
        name: _stdio(
            StdioServerParameters(
                command=sys.executable, args=["-m", f"mcp_servers.{name}.server"], cwd=REPO_ROOT, env=env
            )
        )
        for name in SERVER_NAMES
    }


def _stdio(params: StdioServerParameters) -> Transport:
    return lambda: stdio_client(params)


def _http(url: str) -> Transport:
    return lambda: streamable_http_client(url)


class _Connection:
    """One open session, owned by one task from connect to close."""

    def __init__(self, name: str, transport: Transport) -> None:
        self.name = name
        self._transport = transport
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.session: ClientSession | None = None
        self.tools: dict[str, dict[str, Any]] = {}
        self.error: BaseException | None = None

    @property
    def alive(self) -> bool:
        return self.session is not None and self._task is not None and not self._task.done()

    async def start(self, within_s: float) -> None:
        self._task = asyncio.create_task(self._own(), name=f"mcp:{self.name}")
        try:
            async with asyncio.timeout(within_s):
                await self._ready.wait()
        except TimeoutError:
            await self.stop()
            raise ToolExecutionError(
                self.name, "start", f"did not start within {within_s:.0f}s", kind="unavailable"
            ) from None
        if self.session is None:
            raise ToolExecutionError(
                self.name, "start", f"failed to start: {self.error!r}", kind="unavailable"
            )

    async def _own(self) -> None:
        try:
            async with AsyncExitStack() as stack:
                read, write = await stack.enter_async_context(self._transport())
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                listed = await session.list_tools()
                self.tools = {tool.name: dict(tool.input_schema) for tool in listed.tools}
                self.session = session
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:
            # A dead child surfaces here (often as an ExceptionGroup from the
            # transport's task group); the next call sees `alive` is False.
            self.error = exc
            if not self._stop.is_set():
                log.warning("MCP server %s stopped: %r", self.name, exc)
        finally:
            self.session = None
            self._ready.set()

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        if task is None or task.done():
            return
        try:
            async with asyncio.timeout(SHUTDOWN_TIMEOUT_S):
                await asyncio.shield(task)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class MCPManager:
    """Owns the MCP sessions. Start in the app lifespan, close on shutdown."""

    def __init__(
        self, settings: Settings | None = None, *, transports: dict[str, Transport] | None = None
    ) -> None:
        self._settings = settings or get_settings()
        self._transports = transports if transports is not None else default_transports(self._settings)
        self._conns: dict[str, _Connection] = {}
        self._locks: dict[str, asyncio.Lock] = {name: asyncio.Lock() for name in self._transports}
        self._failed_at: dict[str, float] = {}
        self._closed = False

    @property
    def servers(self) -> tuple[str, ...]:
        return tuple(self._transports)

    async def __aenter__(self) -> MCPManager:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def start(self, *, required: Iterable[str] = ()) -> None:
        """Connect to every server concurrently.

        A server that fails to start is logged and retried on its first call
        (the agent treats its tools as unavailable meanwhile), unless it is in
        ``required``, in which case this raises.
        """
        names = list(self._transports)
        results = await asyncio.gather(*(self._connect(n) for n in names), return_exceptions=True)
        missing = [name for name, r in zip(names, results, strict=True) if isinstance(r, BaseException)]
        for name, r in zip(names, results, strict=True):
            if isinstance(r, BaseException):
                log.warning("MCP server %s is not available: %s", name, r)
            else:
                log.info("MCP server %s ready (%d tools)", name, len(r.tools))
        if needed := sorted(set(required) & set(missing)):
            raise ToolExecutionError(
                ",".join(needed), "start", "required MCP server failed to start", kind="unavailable"
            )

    async def aclose(self) -> None:
        # Set first: a call racing shutdown must not respawn a server nobody will stop.
        self._closed = True
        conns, self._conns = list(self._conns.values()), {}
        await asyncio.gather(*(c.stop() for c in conns), return_exceptions=True)

    def tool_names(self, server: str) -> set[str]:
        conn = self._conns.get(server)
        return set(conn.tools) if conn is not None else set()

    def tool_schemas(self, server: str) -> dict[str, dict[str, Any]]:
        conn = self._conns.get(server)
        return dict(conn.tools) if conn is not None else {}

    async def call(self, server: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Call ``tool`` on ``server`` and return its structured result.

        Raises ``ToolExecutionError``: ``tool_error`` when the server reported
        an error (its message is meant for the model), ``timeout`` after
        ``mcp_call_timeout_s``, ``unavailable`` when the server is down and one
        restart did not bring it back.
        """
        if server not in self._transports:
            raise ToolExecutionError(server, tool, f"unknown MCP server {server!r}", kind="unavailable")
        if self._closed:
            raise self._unavailable(server, tool)
        conn = self._conns.get(server)
        restarted = False
        if conn is None or not conn.alive:
            conn = await self._restart(server, conn, tool)
            restarted = True
        try:
            return await self._call_once(conn, tool, args)
        except _ConnectionLost:
            if restarted:
                raise self._unavailable(server, tool) from None
        conn = await self._restart(server, conn, tool)
        try:
            return await self._call_once(conn, tool, args)
        except _ConnectionLost:
            raise self._unavailable(server, tool) from None

    async def health(self) -> dict[str, ServiceCheck]:
        """``mcp:<server>`` -> reachable or not, for the /health endpoint."""

        async def check(name: str) -> ServiceCheck:
            conn = self._conns.get(name)
            if conn is None or not conn.alive or conn.session is None:
                detail = f"not running: {conn.error!r}" if conn is not None and conn.error else "not running"
                return ServiceCheck(ok=False, detail=detail)
            try:
                async with asyncio.timeout(PING_TIMEOUT_S):
                    await conn.session.send_ping()
            except Exception as exc:
                return ServiceCheck(ok=False, detail=f"ping failed: {exc!r}")
            return ServiceCheck(ok=True, detail=f"{self._settings.mcp_transport}, {len(conn.tools)} tools")

        names = list(self._transports)
        checks = await asyncio.gather(*(check(n) for n in names))
        return {f"mcp:{name}": c for name, c in zip(names, checks, strict=True)}

    # ------------------------------------------------------------ internals

    async def _connect(self, name: str) -> _Connection:
        conn = _Connection(name, self._transports[name])
        self._conns[name] = conn
        try:
            await conn.start(STARTUP_TIMEOUT_S)
        except ToolExecutionError:
            self._failed_at[name] = time.monotonic()
            raise
        self._failed_at.pop(name, None)
        return conn

    async def _restart(self, name: str, dead: _Connection | None, tool: str) -> _Connection:
        async with self._locks[name]:
            if self._closed:
                raise self._unavailable(name, tool)
            current = self._conns.get(name)
            # Another caller may have restarted it while we waited for the lock.
            if current is not None and current is not dead and current.alive:
                return current
            failed_at = self._failed_at.get(name)
            if failed_at is not None and time.monotonic() - failed_at < RESTART_COOLDOWN_S:
                raise self._unavailable(name, tool)
            if current is not None:
                await current.stop()
            log.warning("(re)starting MCP server %s", name)
            try:
                return await self._connect(name)
            except ToolExecutionError as exc:
                raise self._unavailable(name, tool) from exc

    async def _call_once(self, conn: _Connection, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        session = conn.session
        if session is None:
            raise _ConnectionLost
        timeout = self._settings.mcp_call_timeout_s
        try:
            # The session's own read timeout is the real limit; this outer one
            # only guarantees a wedged transport can never hang a turn.
            async with asyncio.timeout(timeout + 5.0):
                result = await session.call_tool(tool, args, read_timeout_seconds=timeout)
        except TimeoutError:
            raise self._timeout(conn.name, tool, timeout) from None
        except MCPError as exc:
            if exc.code == REQUEST_TIMEOUT:
                raise self._timeout(conn.name, tool, timeout) from None
            if exc.code == CONNECTION_CLOSED:
                raise _ConnectionLost from exc
            raise ToolExecutionError(conn.name, tool, exc.message, kind="tool_error") from exc
        except (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream) as exc:
            raise _ConnectionLost from exc
        except RuntimeError as exc:
            # The session re-validates structured content against the tool's
            # output schema; a mismatch is a server bug, reported as a tool error.
            raise ToolExecutionError(conn.name, tool, f"invalid result: {exc}", kind="tool_error") from exc

        text = "\n".join(getattr(block, "text", "") for block in result.content).strip()
        if result.is_error:
            raise ToolExecutionError(
                conn.name, tool, text or "the tool failed without a message", kind="tool_error"
            )
        if isinstance(result.structured_content, dict):
            return result.structured_content
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        raise ToolExecutionError(conn.name, tool, "the tool returned no structured result", kind="tool_error")

    @staticmethod
    def _timeout(server: str, tool: str, timeout: float) -> ToolExecutionError:
        return ToolExecutionError(server, tool, f"timed out after {timeout:.0f}s", kind="timeout")

    @staticmethod
    def _unavailable(server: str, tool: str) -> ToolExecutionError:
        return ToolExecutionError(
            server, tool, f"the {server} server is unavailable right now", kind="unavailable"
        )
