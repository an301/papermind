"""ASGI middleware for what has to happen around the routes rather than in them.

``RequestSizeLimit`` caps request bodies. FastAPI parses a multipart body
completely (spooling it to disk) before any route code or dependency runs, so a
route can only notice a 2 GB upload after receiving all of it. The limit is
therefore enforced on the byte stream itself: a Content-Length over the limit
is refused before anything is read, and a body without one (chunked) is counted
as it arrives and cut off at the limit.

``CatchUnhandled`` turns an exception no handler took into the standard JSON
500 *inside* the CORS middleware (see its docstring).
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from papermind.api.errors import error_response, internal_error_response

log = logging.getLogger(__name__)

_MB = 1024 * 1024
# Multipart framing (boundaries, part headers) on top of the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
# Every other route takes a small JSON document (a question is at most 4,000
# characters); nothing legitimate comes close to this.
JSON_BODY_LIMIT_BYTES = 1 * _MB
# A client that is still sending only sees the 413 if the server reads the
# rest of its body first. Closing the socket with unread data makes the kernel
# answer with a reset, and the client reports a network error instead of the
# message. Reading costs bandwidth but neither memory nor disk, so a body up to
# this multiple of the limit is read and thrown away before answering; past it
# the connection is closed at once.
DRAIN_FACTOR = 2

_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _mb(size_bytes: int) -> str:
    return f"{size_bytes / _MB:.1f}".removesuffix(".0")


def too_large_message(size_bytes: int | None, max_mb: int) -> str:
    """E.g. "That PDF is 72 MB. The limit is 40 MB." (the size is left out when it is unknown)."""
    if size_bytes is None:
        return f"That PDF is larger than the {max_mb} MB limit."
    return f"That PDF is {_mb(size_bytes)} MB. The limit is {max_mb} MB."


def body_too_large_message(limit_bytes: int) -> str:
    return f"The request is larger than the {_mb(limit_bytes)} MB limit."


class _TooLarge(Exception):
    """Raised from ``receive()`` once the body passes the limit. Never leaves the middleware."""


class _BodyGuard:
    """One request's body as the app sees it: counted, and cut off once over the limit."""

    def __init__(self, receive: Receive, send: Send, limit: int) -> None:
        self.raw_receive = receive
        self.raw_send = send
        self.limit = limit
        self.received = 0
        self.body_done = False
        self.exceeded = False
        self.response_started = False

    async def receive(self) -> Message:
        if self.exceeded:
            raise _TooLarge
        message = await self.raw_receive()
        if message["type"] == "http.request":
            self._count(message)
            if self.received > self.limit:
                self.exceeded = True
                raise _TooLarge
        return message

    async def send(self, message: Message) -> None:
        # Once over the limit the middleware owns the response; whatever the
        # app answers to its truncated body (FastAPI's "error parsing the
        # body" 400, say) is dropped.
        if self.exceeded:
            return
        if message["type"] == "http.response.start":
            self.response_started = True
        await self.raw_send(message)

    async def drain(self, up_to: int) -> bool:
        """Read and discard the body until it ends or ``up_to`` bytes in all. True if it ended."""
        while not self.body_done and self.received <= up_to:
            message = await self.raw_receive()
            if message["type"] != "http.request":
                return False  # the client hung up
            self._count(message)
        return self.body_done

    def _count(self, message: Message) -> None:
        self.received += len(message.get("body", b""))
        self.body_done = not message.get("more_body", False)


def _header(scope: Scope, name: bytes) -> bytes | None:
    for key, value in scope["headers"]:
        if key.lower() == name:
            return bytes(value)
    return None


class RequestSizeLimit:
    def __init__(self, app: ASGIApp, *, upload_path: str, max_upload_mb: int) -> None:
        self.app = app
        self.upload_path = upload_path
        self.max_upload_mb = max_upload_mb
        self.max_upload_bytes = max_upload_mb * _MB + MULTIPART_OVERHEAD_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return
        upload = scope["path"] == self.upload_path
        guard = _BodyGuard(receive, send, self.max_upload_bytes if upload else JSON_BODY_LIMIT_BYTES)
        raw_length = _header(scope, b"content-length")
        length = int(raw_length) if raw_length is not None and raw_length.isdigit() else None
        if length is not None and length > guard.limit:
            await self._reject(scope, guard, upload, declared_size=length)
            return
        try:
            await self.app(scope, guard.receive, guard.send)
        except Exception:
            if not guard.exceeded or guard.response_started:
                raise
        if guard.exceeded and not guard.response_started:
            await self._reject(scope, guard, upload, declared_size=None)

    async def _reject(
        self, scope: Scope, guard: _BodyGuard, upload: bool, *, declared_size: int | None
    ) -> None:
        drained = False
        cap = DRAIN_FACTOR * guard.limit
        # A client that sent "Expect: 100-continue" is waiting for permission
        # to send the body, which our first read would give: answer at once.
        # Once anything was read, the server has given it and the body is coming.
        waiting = _header(scope, b"expect") is not None and guard.received == 0
        if not waiting and (declared_size is None or declared_size <= cap):
            drained = await guard.drain(cap)
        size = declared_size if declared_size is not None else (guard.received if drained else None)
        if upload:
            # The body includes the multipart framing, so the size quoted is a
            # few KB high; at this scale nobody can tell.
            code, message = "too_large", too_large_message(size, self.max_upload_mb)
        else:
            code, message = "payload_too_large", body_too_large_message(guard.limit)
        headers = None if drained else {"Connection": "close"}
        response = error_response(413, code, message, headers)
        await response(scope, guard.raw_receive, guard.raw_send)


class CatchUnhandled:
    """Answer an unhandled exception with the standard JSON 500, inside ``CORSMiddleware``.

    Starlette serves the ``Exception`` handler from ``ServerErrorMiddleware``,
    which wraps the whole middleware stack, CORS included. A 500 from there
    leaves without ``Access-Control-Allow-Origin``, so a frontend on another
    origin cannot read it: the browser reports a network failure instead of the
    error body. Registered inside CORS, this produces the same body with the
    CORS headers on. An exception after the response has started can only end
    the connection, so it is re-raised for the server to do that.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            if started:
                raise
            log.error("unhandled error on %s %s", scope.get("method"), scope.get("path"), exc_info=exc)
            await internal_error_response()(scope, receive, send)
