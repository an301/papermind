from __future__ import annotations

import json
from typing import get_args

import fastapi.routing
from fastapi.sse import format_sse_event

from papermind.api.sse import CHAT_EVENT_TYPES, KEEPALIVE_INTERVAL_S, ChatEventModel, frame
from papermind.schemas.events import AnswerDelta, Done, ErrorEvent


def _wire(event: ChatEventModel) -> str:
    sse = frame(event)
    return format_sse_event(data_str=sse.raw_data, event=sse.event).decode()


def test_frame_is_event_name_then_json_data() -> None:
    wire = _wire(ErrorEvent(code="no_api_key", message="Add a key.", retryable=False))
    assert wire == (
        'event: error\ndata: {"type":"error","code":"no_api_key","message":"Add a key.","retryable":false}\n\n'
    )


def test_multiline_text_stays_one_data_line() -> None:
    # JSON escapes the newline, so a frame never splits into several data: lines.
    wire = _wire(AnswerDelta(text="line one\nline two"))
    lines = wire.strip("\n").split("\n")
    assert lines[0] == "event: answer.delta"
    assert len(lines) == 2
    assert json.loads(lines[1].removeprefix("data: "))["text"] == "line one\nline two"


def test_done_frame() -> None:
    assert _wire(Done()) == 'event: done\ndata: {"type":"done"}\n\n'


def test_event_union_is_in_sync_with_the_schema() -> None:
    assert set(CHAT_EVENT_TYPES) == set(get_args(ChatEventModel))
    names = {model.model_fields["type"].default for model in CHAT_EVENT_TYPES}
    assert len(names) == len(CHAT_EVENT_TYPES), "every event has its own wire name"


def test_keepalive_interval_matches_fastapi() -> None:
    assert fastapi.routing._PING_INTERVAL == KEEPALIVE_INTERVAL_S
