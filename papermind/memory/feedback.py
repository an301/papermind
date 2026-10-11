"""Reader feedback on answers ("Report a problem"), as an append-only JSON Lines file.

    {data_dir}/feedback/feedback.jsonl   one report per line, oldest first

A file rather than Redis because a report has to outlive the 24-hour session
it is about: these are the cases worth turning into eval questions. So each
line also carries the turn it is about (question, answer, sources), copied
while the session still exists.

Every API worker appends to the same file. Each record is written with a
single ``write`` on a descriptor opened with ``O_APPEND``, which the kernel
positions atomically at the end of the file, so concurrent reports from
several processes never interleave or overwrite each other.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from papermind.schemas.api import FeedbackRequest
from papermind.schemas.session import ChatMessage, Session
from papermind.schemas.tools import Level


def feedback_path(data_dir: Path) -> Path:
    return data_dir / "feedback" / "feedback.jsonl"


class FeedbackContext(BaseModel):
    """The turn a report is about, as it stood when the report arrived."""

    paper_id: str
    user_id: str
    level: Level | None = None
    question: str | None = None
    answer: str
    cited_chunks: list[str] = Field(default_factory=list, description="chunk ids of the answer's sources")
    error_code: str | None = None


class FeedbackRecord(BaseModel):
    received_at: datetime
    message_id: str
    session_id: str
    reason: str
    note: str
    context: FeedbackContext | None = Field(
        None, description="None when the session had expired or the message was not in it"
    )


def context_for(session: Session | None, message_id: str) -> FeedbackContext | None:
    if session is None:
        return None
    for i, message in enumerate(session.messages):
        if message.message_id == message_id and message.role == "assistant":
            return FeedbackContext(
                paper_id=session.paper_id,
                user_id=session.user_id,
                level=message.level,
                question=_question_before(session.messages, i),
                answer=message.text,
                cited_chunks=[s.chunk_id for s in message.sources],
                error_code=message.error_code,
            )
    return None


def _question_before(messages: list[ChatMessage], index: int) -> str | None:
    for message in reversed(messages[:index]):
        if message.role == "user":
            return message.text
    return None


def build_record(body: FeedbackRequest, session: Session | None) -> FeedbackRecord:
    return FeedbackRecord(
        received_at=datetime.now(UTC),
        message_id=body.message_id,
        session_id=body.session_id,
        reason=body.reason,
        note=body.note.strip(),
        context=context_for(session, body.message_id),
    )


def _append_line(path: Path, line: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        written = os.write(fd, line)
        if written != len(line):  # only a full disk does this to a regular file
            raise OSError(f"short write to {path}: {written} of {len(line)} bytes")
    finally:
        os.close(fd)


async def append_feedback(path: Path, record: FeedbackRecord) -> None:
    payload: dict[str, Any] = record.model_dump(mode="json")
    # ensure_ascii=False keeps quotes and accents readable in the file;
    # json.dumps never emits a raw newline, so one record stays one line.
    line = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    await asyncio.to_thread(_append_line, path, line)
