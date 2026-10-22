"""Request size limits and the catch-all 500, driven at the ASGI level so body streaming is exact."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest

from papermind.api.deps import READER_HEADER
from papermind.api.middleware import DRAIN_FACTOR, JSON_BODY_LIMIT_BYTES
from papermind.graph.store import InMemoryGraphStore
from papermind.graph.testing import sample_catalog
from tests.unit.test_api_support import MINIMAL_PDF, PAPER_ID, READER_ID, make_harness, make_settings

ORIGIN = "http://localhost:5173"
MB = 1024 * 1024
BOUNDARY = "XBOUNDARY"


@dataclass
class Exchange:
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    consumed: int = 0

    @property
    def error(self) -> dict[str, Any]:
        return json.loads(self.body)["error"]


async def exchange(
    app: Any, path: str, chunks: list[bytes], headers: list[tuple[bytes, bytes]], *, method: str = "POST"
) -> Exchange:
    """One request whose body arrives in ``chunks``; records how much of it the server read."""
    out = Exchange()
    queue = list(chunks)

    async def receive() -> dict[str, Any]:
        if queue:
            chunk = queue.pop(0)
            out.consumed += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": bool(queue)}
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            out.status = message["status"]
            out.headers = {k.decode().lower(): v.decode() for k, v in message.get("headers", [])}
        elif message["type"] == "http.response.body":
            out.body += message.get("body", b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"origin", ORIGIN.encode()), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
        "root_path": "",
    }
    await asyncio.wait_for(app(scope, receive, send), 10)
    return out


def multipart(size: int, piece: int = 256 * 1024) -> list[bytes]:
    head = (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="big.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode() + MINIMAL_PDF
    body = [head]
    remaining = size
    while remaining > 0:
        body.append(b"A" * min(piece, remaining))
        remaining -= piece
    body.append(f"\r\n--{BOUNDARY}--\r\n".encode())
    return body


MULTIPART = (b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode())
CHUNKED = (b"transfer-encoding", b"chunked")


def content_length(chunks: list[bytes]) -> tuple[bytes, bytes]:
    return (b"content-length", str(sum(map(len, chunks))).encode())


async def test_a_chunked_upload_is_cut_off_instead_of_spooled_in_full(tmp_path: Path) -> None:
    h = make_harness(tmp_path, settings=make_settings(tmp_path, max_upload_mb=1))
    body = multipart(10 * MB)

    out = await exchange(h.app, "/api/papers/upload", body, [MULTIPART, CHUNKED])

    assert out.status == 413
    assert out.error == {"code": "too_large", "message": "That PDF is larger than the 1 MB limit."}
    # Read up to the drain cap, then the connection is closed: never all 10 MB.
    assert out.consumed <= DRAIN_FACTOR * (MB + 64 * 1024) + 256 * 1024
    assert out.headers["connection"] == "close"
    assert out.headers["access-control-allow-origin"] == ORIGIN
    assert h.ingest.calls == []


async def test_a_small_chunked_overshoot_is_drained_so_the_client_can_read_the_413(tmp_path: Path) -> None:
    h = make_harness(tmp_path, settings=make_settings(tmp_path, max_upload_mb=1))
    body = multipart(MB + 300 * 1024)

    out = await exchange(h.app, "/api/papers/upload", body, [MULTIPART, CHUNKED])

    assert out.status == 413
    assert out.consumed == sum(map(len, body)), "the whole body was read, so no reset"
    assert "connection" not in out.headers
    assert out.error["message"].startswith("That PDF is 1.3 MB.")


async def test_an_oversized_content_length_is_drained_when_close_to_the_limit(tmp_path: Path) -> None:
    h = make_harness(tmp_path, settings=make_settings(tmp_path, max_upload_mb=1))
    body = multipart(MB + 300 * 1024)

    out = await exchange(h.app, "/api/papers/upload", body, [MULTIPART, content_length(body)])

    assert out.status == 413 and out.consumed == sum(map(len, body))
    assert "connection" not in out.headers


async def test_a_far_oversized_content_length_is_refused_without_reading(tmp_path: Path) -> None:
    h = make_harness(tmp_path, settings=make_settings(tmp_path, max_upload_mb=1))
    body = multipart(5 * MB)

    out = await exchange(h.app, "/api/papers/upload", body, [MULTIPART, content_length(body)])

    assert out.status == 413 and out.consumed == 0
    assert out.headers["connection"] == "close"
    assert out.error["message"] == "That PDF is 5 MB. The limit is 1 MB."


async def test_a_client_waiting_on_expect_100_continue_is_answered_at_once(tmp_path: Path) -> None:
    h = make_harness(tmp_path, settings=make_settings(tmp_path, max_upload_mb=1))
    body = multipart(MB + 300 * 1024)
    expect = (b"expect", b"100-continue")

    out = await exchange(h.app, "/api/papers/upload", body, [MULTIPART, content_length(body), expect])

    assert out.status == 413 and out.consumed == 0


async def test_an_upload_under_the_limit_still_goes_through(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False, settings=make_settings(tmp_path, max_upload_mb=1))
    body = multipart(512 * 1024)

    out = await exchange(
        h.app,
        "/api/papers/upload",
        body,
        [MULTIPART, CHUNKED, (READER_HEADER.lower().encode(), READER_ID.encode())],
    )

    assert out.status == 202, out.body
    assert out.consumed == sum(map(len, body))


@pytest.mark.parametrize("framing", ["chunked", "content-length"])
async def test_json_bodies_are_capped_too(tmp_path: Path, framing: str) -> None:
    h = make_harness(tmp_path)
    payload = json.dumps({"session_id": "s_x", "message": "x" * (JSON_BODY_LIMIT_BYTES + 10)}).encode()
    chunks = [payload[i : i + 64 * 1024] for i in range(0, len(payload), 64 * 1024)]
    length = CHUNKED if framing == "chunked" else content_length(chunks)

    out = await exchange(h.app, "/api/chat", chunks, [(b"content-type", b"application/json"), length])

    assert out.status == 413
    assert out.error == {"code": "payload_too_large", "message": "The request is larger than the 1 MB limit."}


async def test_an_unhandled_error_is_the_standard_500_with_cors_headers(tmp_path: Path, caplog) -> None:
    class BrokenGraph(InMemoryGraphStore):
        async def get_user(self, user_id: str):  # type: ignore[override]
            raise ConnectionError("neo4j went away")

    h = make_harness(tmp_path, graph=BrokenGraph(sample_catalog()))
    transport = httpx.ASGITransport(app=h.app, raise_app_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="papermind.api.middleware"):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            broken = await client.get("/api/users/u_x", headers={"Origin": ORIGIN})
            fine = await client.get(f"/api/papers/{PAPER_ID}", headers={"Origin": ORIGIN})

    assert broken.status_code == 500
    assert broken.headers["access-control-allow-origin"] == ORIGIN
    assert broken.json() == {
        "error": {"code": "internal", "message": "Something went wrong on the server. Try again in a moment."}
    }
    assert "unhandled error on GET /api/users/u_x" in caplog.text and "neo4j went away" in caplog.text
    assert fine.status_code == 200


async def test_an_expecting_client_whose_body_is_already_flowing_is_drained(tmp_path: Path) -> None:
    """Chunked + Expect: the server sent 100 Continue on the first read, so the body is on its way."""
    h = make_harness(tmp_path, settings=make_settings(tmp_path, max_upload_mb=1))
    body = multipart(MB + 300 * 1024)
    expect = (b"expect", b"100-continue")

    out = await exchange(h.app, "/api/papers/upload", body, [MULTIPART, CHUNKED, expect])

    assert out.status == 413 and out.consumed == sum(map(len, body))
    assert "connection" not in out.headers
