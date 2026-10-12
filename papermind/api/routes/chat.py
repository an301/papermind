"""POST /api/chat: one question in, one turn of typed events out over SSE."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends

from papermind.api.chat_turn import ChatTurn, open_chat_turn
from papermind.api.errors import error_responses
from papermind.api.guards import within_budget
from papermind.api.sse import EventSourceResponse, frame
from papermind.schemas.events import ChatEvent

router = APIRouter(tags=["chat"])


# The hourly limit is counted in prepare_turn, once the question is known to
# run; a reader turned away by the daily budget uses none of it.
@router.post(
    "/chat",
    response_class=EventSourceResponse,
    responses=error_responses(404, 409, 422, 429, 503),
    dependencies=[Depends(within_budget)],
)
async def chat(turn: Annotated[ChatTurn, Depends(open_chat_turn)]) -> AsyncIterator[ChatEvent]:
    """Stream one turn. Errors before the stream are HTTP errors; after it starts, an
    ``error`` event followed by ``done``.

    The return annotation is what FastAPI publishes as the stream's item
    schema in OpenAPI. The items actually yielded are ``ServerSentEvent``
    wrappers (see ``papermind.api.sse``) so each frame carries ``event: <type>``.
    """
    async with aclosing(turn.events()) as events:
        async for event in events:
            yield frame(event)  # type: ignore[misc]
