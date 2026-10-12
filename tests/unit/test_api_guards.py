"""The public-deployment guards: access code, rate limits, daily budget, delete switch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fakeredis

from papermind.api.guards import ACCESS_HEADER, visitor_key
from papermind.memory.usage import RateLimiter, SpendMeter
from papermind.schemas.session import Session
from tests.unit.test_api_support import PAPER_ID, FakeRunner, Harness, make_harness, make_settings

CODE = "reading-group-2026"


def _harness(tmp_path: Path, **settings: Any) -> Harness:
    h = make_harness(
        tmp_path,
        runner=FakeRunner(),
        settings=make_settings(tmp_path, anthropic_api_key="sk-test", **settings),
    )
    h.res.limiter = RateLimiter(h.redis)
    h.res.spend = SpendMeter(h.redis, budget_usd=h.res.settings.daily_budget_usd)
    return h


async def _session(h: Harness) -> Session:
    user = await h.add_user()
    return await h.res.sessions.create(user.user_id, PAPER_ID, user.level)


async def _ask(h: Harness, session_id: str, headers: dict[str, str] | None = None) -> Any:
    async with h.client() as client:
        return await client.post(
            "/api/chat", json={"session_id": session_id, "message": "What is attention?"}, headers=headers
        )


# ---------------------------------------------------------------- access code


async def test_no_code_configured_leaves_everything_open(tmp_path: Path) -> None:
    h = _harness(tmp_path)
    async with h.client() as client:
        assert (await client.get("/api/papers")).status_code == 200
        assert (await client.get("/api/access")).status_code == 204
        health = (await client.get("/api/health")).json()
    assert health["access_code_required"] is False
    assert health["paper_delete_allowed"] is True


async def test_code_required_everywhere_but_health(tmp_path: Path) -> None:
    h = _harness(tmp_path, access_code=CODE)
    async with h.client() as client:
        health = await client.get("/api/health")
        missing = await client.get("/api/papers")
        wrong = await client.get("/api/papers", headers={ACCESS_HEADER: "nope"})
        right = await client.get("/api/papers", headers={ACCESS_HEADER: CODE})
        check = await client.get("/api/access", headers={ACCESS_HEADER: f"  {CODE} "})
    assert health.status_code == 200 and health.json()["access_code_required"] is True
    assert missing.status_code == 401 and missing.json()["error"]["code"] == "access_code_required"
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "access_code_invalid"
    assert right.status_code == 200
    assert check.status_code == 204


async def test_chat_and_pdf_are_behind_the_code(tmp_path: Path) -> None:
    h = _harness(tmp_path, access_code=CODE)
    session = await _session(h)
    async with h.client() as client:
        pdf = await client.get(f"/api/papers/{PAPER_ID}/pdf")
    assert pdf.status_code == 401
    assert (await _ask(h, session.session_id)).status_code == 401
    assert (await _ask(h, session.session_id, {ACCESS_HEADER: CODE})).status_code == 200


async def test_guessing_codes_locks_the_visitor_out_even_for_the_right_code(tmp_path: Path) -> None:
    h = _harness(tmp_path, access_code=CODE)
    async with h.client() as client:
        missing = [(await client.get("/api/papers")).status_code for _ in range(25)]
        guesses = [
            (await client.get("/api/papers", headers={ACCESS_HEADER: f"guess-{i}"})).status_code
            for i in range(21)
        ]
        # Locked out: a right guess now looks exactly like a wrong one.
        right = await client.get("/api/access", headers={ACCESS_HEADER: CODE})
        health = await client.get("/api/health")
    assert set(missing) == {401}  # a missing code is not a guess
    assert guesses[:20] == [401] * 20
    assert guesses[20] == 429
    assert right.status_code == 429
    assert health.status_code == 200


async def test_an_upload_without_the_code_is_refused_before_its_body_is_read(tmp_path: Path) -> None:
    h = _harness(tmp_path, access_code=CODE)
    async with h.client() as client:
        response = await client.post(
            "/api/papers/upload", files={"file": ("x.pdf", b"%PDF-1.4" + b"0" * 1024, "application/pdf")}
        )
    assert response.status_code == 401
    assert response.headers["connection"] == "close"


def test_ipv6_visitors_are_counted_per_64() -> None:
    assert visitor_key("2001:db8:1:2:aaaa::1") == visitor_key("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    assert visitor_key("203.0.113.9") == "203.0.113.9"
    assert visitor_key(None) == "unknown"


# ---------------------------------------------------------------- rate limits


async def test_refused_questions_do_not_use_up_the_hour(tmp_path: Path) -> None:
    h = _harness(tmp_path, questions_per_hour=1)
    session = await _session(h)
    async with h.client() as client:
        bad = await client.post("/api/chat", json={"session_id": "s_nope", "message": "hi"})
    assert bad.status_code == 404
    assert (await _ask(h, session.session_id)).status_code == 200


async def test_questions_per_hour(tmp_path: Path) -> None:
    h = _harness(tmp_path, questions_per_hour=2)
    session = await _session(h)
    statuses = [(await _ask(h, session.session_id)).status_code for _ in range(3)]
    assert statuses == [200, 200, 429]
    over = await _ask(h, session.session_id)
    body = over.json()["error"]
    assert body["code"] == "rate_limited"
    assert "2 questions this hour" in body["message"]
    assert int(over.headers["Retry-After"]) > 0


async def test_papers_per_day_counts_only_new_papers(tmp_path: Path) -> None:
    h = _harness(tmp_path, papers_per_day=1)
    h.res.arxiv = None  # skip the arXiv lookup
    async with h.client() as client:
        # Already in the library: opening it again is free.
        existing = [await client.post("/api/papers", json={"arxiv": PAPER_ID}) for _ in range(3)]
        first = await client.post("/api/papers", json={"arxiv": "1810.04805"})
        second = await client.post("/api/papers", json={"arxiv": "1512.03385"})
    assert [r.status_code for r in existing] == [200, 200, 200]
    assert first.status_code == 202
    assert second.status_code == 429
    assert "1 papers today" in second.json()["error"]["message"]


async def test_limiter_windows_reset() -> None:
    limiter = RateLimiter(fakeredis.FakeAsyncRedis(decode_responses=True))
    assert await limiter.hit("q", "1.2.3.4", limit=1, window_s=3600, now=0) is None
    assert await limiter.hit("q", "1.2.3.4", limit=1, window_s=3600, now=10) == 3590
    assert await limiter.hit("q", "5.6.7.8", limit=1, window_s=3600, now=10) is None
    assert await limiter.hit("q", "1.2.3.4", limit=1, window_s=3600, now=3600) is None


async def test_limiter_without_redis_counts_in_process() -> None:
    limiter = RateLimiter()
    assert await limiter.hit("q", "a", limit=1, window_s=60, now=0) is None
    assert await limiter.hit("q", "a", limit=1, window_s=60, now=1) is not None


# ---------------------------------------------------------------- daily budget


async def test_spend_meter_adds_up_across_instances() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    a, b = SpendMeter(redis, budget_usd=1.0), SpendMeter(redis, budget_usd=1.0)
    a.record(0.4)
    b.record(0.5)
    await a.aclose()
    await b.aclose()
    assert abs(await a.spent_today() - 0.9) < 1e-9
    assert not await a.over_budget()
    a.record(0.2)
    await a.aclose()
    assert await b.over_budget()


async def test_zero_budget_never_blocks() -> None:
    meter = SpendMeter(budget_usd=0.0)
    meter.record(1000.0)
    assert not await meter.over_budget()


async def test_over_budget_stops_chat_without_using_a_question(tmp_path: Path) -> None:
    h = _harness(tmp_path, daily_budget_usd=0.5, questions_per_hour=1)
    session = await _session(h)
    h.res.spend.record(0.6)
    await h.res.spend.aclose()
    refused = await _ask(h, session.session_id)
    assert refused.status_code == 503
    assert refused.json()["error"]["code"] == "daily_limit_reached"
    # Lift the ceiling: the refused question did not count against the hourly limit.
    h.res.spend.budget_usd = 0
    assert (await _ask(h, session.session_id)).status_code == 200


async def test_over_budget_suggestions_fall_back_to_free_ones(tmp_path: Path) -> None:
    h = _harness(tmp_path, daily_budget_usd=0.1)
    h.res.spend.record(0.2)
    await h.res.spend.aclose()
    async with h.client() as client:
        response = await client.get(f"/api/papers/{PAPER_ID}/suggestions")
    assert response.status_code == 200
    assert response.json()["questions"]


# ---------------------------------------------------------------- delete switch


async def test_delete_can_be_turned_off(tmp_path: Path) -> None:
    h = _harness(tmp_path, allow_paper_delete=False)
    async with h.client() as client:
        refused = await client.delete(f"/api/papers/{PAPER_ID}")
        still_there = await client.get(f"/api/papers/{PAPER_ID}")
        health = (await client.get("/api/health")).json()
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "delete_disabled"
    assert still_there.status_code == 200
    assert health["paper_delete_allowed"] is False
