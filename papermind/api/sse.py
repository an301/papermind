"""ChatEvent models -> Server-Sent Event frames.

Transport is FastAPI's built-in SSE (``response_class=EventSourceResponse`` on
an async-generator route). It already does the parts that are easy to get
wrong: ``Cache-Control: no-cache`` and ``X-Accel-Buffering: no`` so nginx-style
proxies do not buffer the stream, a ``: ping`` comment after 15 idle seconds so
idle-timeout proxies keep the connection open while the agent works, and
cancelling the generator when the client disconnects.

What it does not do by default is name events: a plain model is sent as a bare
``data:`` line. The contract in ``papermind/schemas/events.py`` is one frame per
event with ``event: <type>``, so each model is wrapped in a ``ServerSentEvent``.
FastAPI skips item validation for that wrapper, which is fine: every event is
already a validated model when it is constructed.
"""

from __future__ import annotations

from typing import TypeAlias, get_args

from fastapi.sse import KEEPALIVE_COMMENT, EventSourceResponse, ServerSentEvent

from papermind.schemas.events import (
    AnswerDelta,
    AnswerDone,
    ChatEvent,
    Done,
    ErrorEvent,
    Followups,
    GapsFound,
    KnowledgeUpdated,
    SourcesReady,
    ToolFinished,
    ToolRetry,
    ToolStarted,
    TurnStarted,
    UsageReport,
)

__all__ = [
    "CHAT_EVENT_TYPES",
    "KEEPALIVE_COMMENT",
    "KEEPALIVE_INTERVAL_S",
    "SSE_HEADERS",
    "ChatEventModel",
    "EventSourceResponse",
    "frame",
]

# The plain union of the event models (``ChatEvent`` is the same union wrapped
# in a discriminator annotation, which is not usable in isinstance or as a
# type argument everywhere). tests/unit/test_api_sse.py keeps the two in sync.
ChatEventModel: TypeAlias = (
    TurnStarted
    | ToolStarted
    | ToolRetry
    | ToolFinished
    | GapsFound
    | SourcesReady
    | AnswerDelta
    | AnswerDone
    | KnowledgeUpdated
    | Followups
    | UsageReport
    | ErrorEvent
    | Done
)

CHAT_EVENT_TYPES: tuple[type, ...] = get_args(get_args(ChatEvent)[0])

# FastAPI's built-in interval; restated here because the frontend's stall
# detector is tuned against it.
KEEPALIVE_INTERVAL_S = 15.0

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def frame(event: ChatEventModel) -> ServerSentEvent:
    """The SSE frame for one event: ``event: <type>`` and the model's JSON as ``data:``."""
    # raw_data, because data= would JSON-encode the already-encoded string again.
    return ServerSentEvent(raw_data=event.model_dump_json(), event=event.type)
