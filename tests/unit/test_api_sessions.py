from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from papermind.memory.session_store import lock_key, session_key
from papermind.schemas.paper import PaperMeta
from tests.unit.test_api_support import PAPER_ID, make_harness, make_settings


async def test_create_uses_the_profile_level_by_default(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user(level="beginner")
    async with h.client() as client:
        default = await client.post("/api/sessions", json={"user_id": "u_reader", "paper_id": PAPER_ID})
        explicit = await client.post(
            "/api/sessions", json={"user_id": "u_reader", "paper_id": PAPER_ID, "level": "expert"}
        )
    assert default.status_code == 200
    body = default.json()
    assert body["session_id"].startswith("s_")
    assert (body["user_id"], body["paper_id"], body["level"]) == ("u_reader", PAPER_ID, "beginner")
    assert body["messages"] == [] and body["scroll"] == {"page": 0, "offset": 0.0, "zoom": None}
    assert explicit.json()["level"] == "expert"


async def test_create_checks_user_and_paper(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    await h.res.ingest_status.claim("2401.00001", source="arxiv", title="arXiv:2401.00001")
    async with h.client() as client:
        no_user = await client.post("/api/sessions", json={"user_id": "u_nobody", "paper_id": PAPER_ID})
        no_paper = await client.post("/api/sessions", json={"user_id": "u_reader", "paper_id": "9999.99999"})
        not_ready = await client.post("/api/sessions", json={"user_id": "u_reader", "paper_id": "2401.00001"})
    assert (no_user.status_code, no_user.json()["error"]["code"]) == (404, "user_not_found")
    assert (no_paper.status_code, no_paper.json()["error"]["code"]) == (404, "paper_not_found")
    assert (not_ready.status_code, not_ready.json()["error"]["code"]) == (409, "paper_not_ready")
    assert "still being processed" in not_ready.json()["error"]["message"]


async def test_get_and_patch(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    async with h.client() as client:
        created = (
            await client.post("/api/sessions", json={"user_id": "u_reader", "paper_id": PAPER_ID})
        ).json()
        sid = created["session_id"]
        scrolled = await client.patch(f"/api/sessions/{sid}", json={"scroll": {"page": 4, "offset": 0.25}})
        leveled = await client.patch(f"/api/sessions/{sid}", json={"level": "expert"})
        unchanged = await client.patch(f"/api/sessions/{sid}", json={})
        fetched = await client.get(f"/api/sessions/{sid}")
    assert scrolled.status_code == 200 and scrolled.json()["scroll"]["page"] == 4
    assert leveled.json()["level"] == "expert"
    assert leveled.json()["scroll"]["page"] == 4, "a level change keeps the scroll position"
    assert unchanged.json()["level"] == "expert"
    assert fetched.json()["scroll"] == {"page": 4, "offset": 0.25, "zoom": None}


async def test_patch_refreshes_the_ttl(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    session = await h.res.sessions.create("u_reader", PAPER_ID, "intermediate")
    await h.redis.expire(session_key(session.session_id), 30)
    async with h.client() as client:
        await client.patch(f"/api/sessions/{session.session_id}", json={"scroll": {"page": 1}})
    assert await h.redis.ttl(session_key(session.session_id)) > h.res.settings.session_ttl_seconds - 5


async def test_missing_and_invalid(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        missing = await client.get("/api/sessions/s_gone")
        patch_missing = await client.patch("/api/sessions/s_gone", json={"level": "expert"})
        bad_scroll = await client.patch("/api/sessions/s_gone", json={"scroll": {"page": -1}})
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "session_not_found")
    assert "expired" in missing.json()["error"]["message"]
    assert patch_missing.status_code == 404
    assert bad_scroll.status_code == 422
    assert bad_scroll.json()["error"]["message"].startswith("scroll.page:")


async def test_patch_while_another_request_holds_the_lock_is_a_409(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.res.sessions._lock_wait_s = 0.1  # type: ignore[attr-defined]
    session = await h.res.sessions.create("u_reader", PAPER_ID, "intermediate")
    await h.redis.set(lock_key(session.session_id), "other", px=10_000)
    async with h.client() as client:
        response = await client.patch(f"/api/sessions/{session.session_id}", json={"level": "expert"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_busy"


async def test_prewarm_runs_in_the_background_without_delaying_the_response(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    warmed: list[str] = []

    async def prewarm(paper: PaperMeta) -> Any:
        started.set()
        await release.wait()
        warmed.append(paper.paper_id)

    settings = make_settings(tmp_path, anthropic_api_key="sk-test", prewarm_cache=True)
    h = make_harness(tmp_path, settings=settings)
    h.res.prewarm = prewarm
    await h.add_user()
    async with h.client() as client:
        response = await asyncio.wait_for(
            client.post("/api/sessions", json={"user_id": "u_reader", "paper_id": PAPER_ID}), 2
        )
    assert response.status_code == 200
    await asyncio.wait_for(started.wait(), 1)
    assert warmed == [] and len(h.res.background) == 1
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert warmed == [PAPER_ID]
    assert len(h.res.background) == 0


async def test_no_prewarm_without_a_key_or_when_disabled(tmp_path: Path) -> None:
    calls: list[str] = []

    async def prewarm(paper: PaperMeta) -> None:
        calls.append(paper.paper_id)

    for settings in (
        make_settings(tmp_path, anthropic_api_key=None, prewarm_cache=True),
        make_settings(tmp_path, anthropic_api_key="sk-test", prewarm_cache=False),
    ):
        h = make_harness(tmp_path, settings=settings)
        h.res.prewarm = prewarm
        await h.add_user()
        async with h.client() as client:
            assert (
                await client.post("/api/sessions", json={"user_id": "u_reader", "paper_id": PAPER_ID})
            ).status_code == 200
    assert calls == []


async def test_a_failing_prewarm_is_logged_not_raised(tmp_path: Path, caplog) -> None:
    async def prewarm(paper: PaperMeta) -> None:
        raise RuntimeError("overloaded")

    h = make_harness(
        tmp_path, settings=make_settings(tmp_path, anthropic_api_key="sk-test", prewarm_cache=True)
    )
    h.res.prewarm = prewarm
    await h.add_user()
    async with h.client() as client:
        response = await client.post("/api/sessions", json={"user_id": "u_reader", "paper_id": PAPER_ID})
    await asyncio.sleep(0.01)
    assert response.status_code == 200
    assert "prewarm" in caplog.text and "overloaded" in caplog.text
