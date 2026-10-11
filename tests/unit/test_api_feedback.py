from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from papermind.memory.feedback import feedback_path
from papermind.schemas.session import Session
from tests.unit.test_api_support import PAPER_ID, FakeRunner, Harness, make_harness


def _lines(h: Harness) -> list[dict[str, Any]]:
    path = feedback_path(h.res.settings.data_dir)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def _answered_session(h: Harness) -> Session:
    user = await h.add_user()
    session = await h.res.sessions.create(user.user_id, PAPER_ID, user.level)
    async with h.client() as client:
        await client.post(
            "/api/chat", json={"session_id": session.session_id, "message": "Why scale by √d_k?"}
        )
    found = await h.res.sessions.get(session.session_id)
    assert found is not None
    return found


async def test_feedback_is_appended_with_a_timestamp_and_the_turn_it_is_about(tmp_path: Path) -> None:
    h = make_harness(tmp_path, runner=FakeRunner())
    session = await _answered_session(h)
    before = datetime.now(UTC)

    async with h.client() as client:
        response = await client.post(
            "/api/feedback",
            json={
                "message_id": "m_answer",
                "session_id": session.session_id,
                "reason": "bad_citation",
                "note": "  [S1] points at the wrong paragraph.  ",
            },
        )

    assert response.status_code == 204 and response.content == b""
    (record,) = _lines(h)
    assert datetime.fromisoformat(record.pop("received_at")) - before < timedelta(seconds=5)
    assert record == {
        "message_id": "m_answer",
        "session_id": session.session_id,
        "reason": "bad_citation",
        "note": "[S1] points at the wrong paragraph.",
        "context": {
            "paper_id": PAPER_ID,
            "user_id": "u_reader",
            "level": "intermediate",
            "question": "Why scale by √d_k?",
            "answer": "Attention weighs values [S1].",
            "cited_chunks": [f"{PAPER_ID}:sec_3.2_scaled_dot_product_attention"],
            "error_code": None,
        },
    }


async def test_feedback_on_an_expired_session_is_still_kept(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        response = await client.post(
            "/api/feedback", json={"message_id": "m_gone", "session_id": "s_expired", "reason": "wrong"}
        )
    assert response.status_code == 204
    (record,) = _lines(h)
    assert record["message_id"] == "m_gone" and record["note"] == "" and record["context"] is None


async def test_feedback_survives_a_session_store_outage(tmp_path: Path) -> None:
    h = make_harness(tmp_path)

    async def down(session_id: str) -> Session | None:
        raise ConnectionError("redis went away")

    h.res.sessions.get = down  # type: ignore[method-assign]
    async with h.client() as client:
        response = await client.post(
            "/api/feedback", json={"message_id": "m_1", "session_id": "s_1", "reason": "unclear"}
        )
    assert response.status_code == 204
    assert _lines(h)[0]["context"] is None


async def test_concurrent_reports_each_get_one_whole_line(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    note = "x" * 1900  # long lines are where interleaved writes would show
    async with h.client() as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/api/feedback",
                    json={"message_id": f"m_{i}", "session_id": "s_1", "reason": "other", "note": note},
                )
                for i in range(25)
            )
        )
    assert {r.status_code for r in responses} == {204}
    records = _lines(h)
    assert sorted(r["message_id"] for r in records) == sorted(f"m_{i}" for i in range(25))
    assert all(r["note"] == note for r in records)


async def test_invalid_feedback_is_a_readable_422(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        unknown_reason = await client.post(
            "/api/feedback", json={"message_id": "m_1", "session_id": "s_1", "reason": "rude"}
        )
        long_note = await client.post(
            "/api/feedback",
            json={"message_id": "m_1", "session_id": "s_1", "reason": "other", "note": "x" * 2001},
        )
    assert unknown_reason.status_code == long_note.status_code == 422
    assert unknown_reason.json()["error"]["message"].startswith("reason:")
    assert not feedback_path(h.res.settings.data_dir).exists()
