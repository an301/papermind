from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from papermind.memory.feedback import append_feedback, build_record, context_for, feedback_path
from papermind.schemas.api import FeedbackRequest
from papermind.schemas.session import ChatMessage, Session

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def _message(message_id: str, role: str, text: str, **extra: object) -> ChatMessage:
    return ChatMessage(message_id=message_id, role=role, text=text, created_at=NOW, **extra)


def _session(*messages: ChatMessage) -> Session:
    return Session(
        session_id="s_1",
        user_id="u_1",
        paper_id="1706.03762",
        level="beginner",
        messages=list(messages),
        created_at=NOW,
        updated_at=NOW,
    )


def test_context_pairs_the_answer_with_the_question_before_it() -> None:
    session = _session(
        _message("m_q1", "user", "What is attention?"),
        _message("m_a1", "assistant", "A weighted average.", level="beginner"),
        _message("m_q2", "user", "And multi-head?"),
        _message("m_a2", "assistant", "", error="The model is busy.", error_code="overloaded"),
    )
    first = context_for(session, "m_a1")
    assert first is not None and (first.question, first.answer, first.level) == (
        "What is attention?",
        "A weighted average.",
        "beginner",
    )
    failed = context_for(session, "m_a2")
    assert failed is not None and failed.question == "And multi-head?" and failed.error_code == "overloaded"


def test_no_context_for_unknown_messages_or_for_questions() -> None:
    session = _session(_message("m_q1", "user", "What is attention?"))
    assert context_for(session, "m_missing") is None
    assert context_for(session, "m_q1") is None, "feedback is about answers"
    assert context_for(None, "m_q1") is None


async def test_append_creates_the_directory_and_writes_one_line_per_record(tmp_path: Path) -> None:
    path = feedback_path(tmp_path / "data")
    for reason in ("wrong", "wrong_level"):
        body = FeedbackRequest(message_id="m_1", session_id="s_1", reason=reason, note="línea\nnueva")
        await append_feedback(path, build_record(body, None))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["reason"] for line in lines] == ["wrong", "wrong_level"]
    assert json.loads(lines[0])["note"] == "línea\nnueva", "a newline in the note stays inside its record"
    assert "línea" in lines[0], "written as UTF-8, not \\u escapes"
