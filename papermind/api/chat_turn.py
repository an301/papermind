"""One chat turn, from a validated request to a finished stream of ChatEvents.

Split in two because HTTP only allows an error status before the first byte:

* ``prepare_turn`` is a FastAPI dependency, so it runs before the response
  starts. Everything that can fail with a clean 404/409 happens here: the
  session exists, its paper is ready, and the reader's message is persisted
  (so a reload mid-answer still shows the question).
* ``ChatTurn.events`` runs inside the stream. From here on a failure can only
  be reported in-band, so every exception becomes an ``error`` event and the
  stream always ends with ``done``.

The agent runs in its own task and hands events over through a queue, so a
client disconnect cancels the turn explicitly here (``ChatTurn.close``) rather
than leaving it running, billing tokens nobody will read.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Annotated, Any, cast

import anyio
from fastapi import Depends, Request

from papermind.api.deps import Resources, ResourcesDep
from papermind.api.guards import limit_questions
from papermind.api.lookups import require_ready_paper, require_session
from papermind.api.sse import ChatEventModel
from papermind.graph.store import GraphStore
from papermind.memory.session_store import new_message_id, utcnow
from papermind.memory.users import ensure_user
from papermind.schemas.api import ChatRequest
from papermind.schemas.events import (
    AnswerDelta,
    AnswerDone,
    Done,
    ErrorCode,
    ErrorEvent,
    ExternalRef,
    Followups,
    GapsFound,
    SourceRef,
    SourcesReady,
    ToolFinished,
    ToolStarted,
    TurnStarted,
    UsageReport,
)
from papermind.schemas.paper import PaperMeta
from papermind.schemas.session import ActivityItem, ChatMessage, KnownConcept, Session, UserProfile
from papermind.schemas.tools import Level

log = logging.getLogger(__name__)

NO_API_KEY_MESSAGE = (
    "PaperMind can't answer questions yet because no Anthropic API key is configured. "
    "The owner of this server needs to add ANTHROPIC_API_KEY to the .env file in the project root "
    "and restart the server."
)
INTERRUPTED_MESSAGE = "The answer was interrupted before it finished."
# ChatMessage.error_code for an answer cut short by a disconnect or the
# reader's Stop. Not an ErrorCode: no error event is ever streamed for it (the
# client already knows it hung up); it exists so a reloaded transcript can say
# "stopped partway" rather than show a failure.
INTERRUPTED_CODE = "interrupted"
NO_ANSWER_MESSAGE = "No answer was produced for this question. Try asking again."

# Plain-language fallbacks per code, for exceptions that carry a code but no
# message meant for the reader.
_DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    "no_api_key": NO_API_KEY_MESSAGE,
    "auth_failed": "The Anthropic API rejected the API key. Check ANTHROPIC_API_KEY in .env.",
    "rate_limited": "The model is rate-limited right now. Wait a few seconds and ask again.",
    "overloaded": "The model is overloaded right now. Try again in a minute.",
    "api_error": "The model API returned an error. Try asking again.",
    "refusal": "The model declined to answer this. Try rephrasing the question.",
    "tool_failed": "One of the research tools failed while answering. Try asking again.",
    "internal": "Something went wrong on the server while answering. Try asking again.",
}
_RETRYABLE: frozenset[ErrorCode] = frozenset(
    {"rate_limited", "overloaded", "api_error", "tool_failed", "internal"}
)
_ERROR_CODES: frozenset[str] = frozenset(_DEFAULT_MESSAGES) | {"paper_not_ready", "session_not_found"}

# Bounded waits for cleanup that must outlive a cancelled request.
RUNNER_CANCEL_TIMEOUT_S = 5.0
PERSIST_TIMEOUT_S = 5.0
# A healthy graph answers a profile or knowledge read in milliseconds. When
# Neo4j has gone away the driver keeps retrying for its default 30 s before it
# raises, and a question can be answered without either read, so a turn waits
# no longer than this for them.
GRAPH_READ_TIMEOUT_S = 3.0

ContextFactory = Callable[..., Any]


def default_context_factory(**fields: Any) -> Any:
    from papermind.agent.loop import TurnContext

    return TurnContext(**fields)


def new_turn_id() -> str:
    return "t_" + secrets.token_urlsafe(9)


def error_event_for(exc: BaseException) -> ErrorEvent:
    """Any exception -> an error event the reader can act on (never a stack trace)."""
    raw_code = getattr(exc, "code", None)
    if isinstance(raw_code, str) and raw_code in _ERROR_CODES:
        code = cast(ErrorCode, raw_code)
        message = getattr(exc, "message", None)
        retryable = getattr(exc, "retryable", None)
        return ErrorEvent(
            code=code,
            message=message
            if isinstance(message, str) and message
            else _DEFAULT_MESSAGES.get(code, str(exc)),
            retryable=retryable if isinstance(retryable, bool) else code in _RETRYABLE,
        )
    return ErrorEvent(code="internal", message=_DEFAULT_MESSAGES["internal"], retryable=True)


# ---------------------------------------------------------------- graph reads


async def read_knowledge(graph: GraphStore, user_id: str) -> dict[str, KnownConcept]:
    """``graph.known`` bounded by ``GRAPH_READ_TIMEOUT_S`` (raises ``TimeoutError`` past it).

    Also the runner's ``knowledge_source``, so the mastery pass after the
    answer cannot hold the stream open for the driver's full retry window.
    """
    async with asyncio.timeout(GRAPH_READ_TIMEOUT_S):
        return await graph.known(user_id)


async def load_user(graph: GraphStore, session: Session) -> UserProfile | None:
    """The reader's profile (recreated if the graph lost it), or None if the graph cannot be read now."""
    try:
        async with asyncio.timeout(GRAPH_READ_TIMEOUT_S):
            return await ensure_user(graph, session.user_id, level=session.level)
    except Exception:
        log.warning(
            "could not read %s's profile; answering without the graph", session.user_id, exc_info=True
        )
        return None


# ---------------------------------------------------------------- before the stream


@dataclass
class PreparedTurn:
    request: ChatRequest
    session: Session
    """The session as the agent should see it: the new level, history *before* this question."""
    paper: PaperMeta
    user: UserProfile
    level: Level
    user_message: ChatMessage
    graph_available: bool = True
    """False when the profile read already failed; the turn then skips its other graph reads."""


async def prepare_turn(body: ChatRequest, res: ResourcesDep, request: Request) -> PreparedTurn:
    session = await require_session(res, body.session_id)
    paper = await require_ready_paper(res, session.paper_id)
    # Counted only for a question that will run: a refused one (bad session,
    # paper not ready) does not use up the reader's hour.
    await limit_questions(request, res)
    user = await load_user(res.graph, session)
    graph_available = user is not None
    if user is None:
        # Enough for the agent: it only needs the id and the level, and the
        # session has both.
        user = UserProfile(user_id=session.user_id, level=session.level, created_at=utcnow())
    question = ChatMessage(
        message_id=new_message_id(),
        role="user",
        text=body.message,
        quote=body.quote,
        created_at=utcnow(),
    )

    def append_question(current: Session) -> Session:
        # The level is resolved here, under the session lock, from the document
        # as it is now: a level PATCH that landed after the read above must
        # not be overwritten with the value that read saw.
        level = body.level or current.level
        message = question.model_copy(update={"level": level})
        return current.model_copy(update={"level": level, "messages": [*current.messages, message]})

    # SessionNotFound / SessionBusy here become a 404 / 409 response.
    persisted = await res.sessions.update(session.session_id, append_question)
    history = persisted.model_copy(update={"messages": persisted.messages[:-1]})
    return PreparedTurn(
        request=body,
        session=history,
        paper=paper,
        user=user,
        level=persisted.level,
        user_message=persisted.messages[-1],
        graph_available=graph_available,
    )


# ---------------------------------------------------------------- the transcript


class TranscriptRecorder:
    """Builds the assistant's ChatMessage from the events the reader saw.

    A reloaded transcript has to render the same turn the stream did: the
    passage cards and brackets (sources), the external-paper cards, the gap
    callout, the collapsed trail with its total time, and the follow-ups.
    """

    def __init__(self, level: Level) -> None:
        self.level = level
        self.message_id: str | None = None
        self.deltas: list[str] = []
        self.final_text: str | None = None
        self.sources: list[SourceRef] = []
        self.external: list[ExternalRef] = []
        self.gaps: GapsFound | None = None
        self.activity: dict[str, ActivityItem] = {}
        self.followups: list[str] = []
        self.latency_ms: int | None = None
        self.error: str | None = None
        self.error_code: str | None = None

    def observe(self, event: ChatEventModel) -> None:
        match event:
            case ToolStarted():
                self.activity[event.call_id] = ActivityItem(tool=event.tool, label=event.label)
            case ToolFinished():
                item = self.activity.get(event.call_id) or ActivityItem(tool=event.tool, label=event.tool)
                self.activity[event.call_id] = item.model_copy(
                    update={"summary": event.summary, "ok": event.ok, "duration_ms": event.duration_ms}
                )
            case GapsFound():
                self.gaps = event
            case SourcesReady():
                self.sources = list(event.sources)
            case AnswerDelta():
                self.deltas.append(event.text)
            case AnswerDone():
                self.final_text = event.text
                self.message_id = event.message_id
                # The final list carries cited_sentence, which the earlier
                # 'sources' event cannot have (the answer did not exist yet).
                # Empty means the runner did not restate them, not "no sources".
                if event.sources:
                    self.sources = list(event.sources)
                self.external = list(event.external)
            case Followups():
                self.followups = list(event.questions)
            case UsageReport():
                self.latency_ms = event.latency_ms
            case ErrorEvent():
                self.error = event.message
                self.error_code = event.code
            case _:
                pass

    @property
    def text(self) -> str:
        return self.final_text if self.final_text is not None else "".join(self.deltas)

    def message(self, *, interrupted: bool = False, elapsed_ms: int | None = None) -> ChatMessage:
        """The assistant message to persist.

        ``elapsed_ms`` (the API's own measure of the turn) stands in for the
        runner's ``usage.latency_ms`` when the turn ended before reporting it:
        the runner sends an error instead of usage, and a disconnect can land
        between answer.done and usage.
        """
        error, error_code = self.error, self.error_code
        if interrupted and self.final_text is None and error is None:
            error, error_code = INTERRUPTED_MESSAGE, INTERRUPTED_CODE
        elif not self.text and error is None:
            error, error_code = NO_ANSWER_MESSAGE, "internal"
        return ChatMessage(
            message_id=self.message_id or new_message_id(),
            role="assistant",
            text=self.text,
            level=self.level,
            sources=self.sources,
            external=self.external,
            gaps=self.gaps,
            activity=list(self.activity.values()),
            followups=self.followups,
            latency_ms=self.latency_ms if self.latency_ms is not None else elapsed_ms,
            error=error,
            error_code=error_code,
            created_at=utcnow(),
        )


# ---------------------------------------------------------------- the stream

_END = object()
# The API frames the stream itself: it emits the one turn.started (at once,
# before any graph or model call) and the one done (after persisting), so the
# runner's own copies are dropped. Both carry the same ids: the runner is
# handed this turn's turn_id and user_message_id.
_FRAMING = frozenset({"turn.started", "done"})


class ChatTurn:
    """One turn's stream plus the cleanup that must happen exactly once.

    ``close()`` stops the agent and persists the answer. It runs from the
    stream's ``finally`` when the stream ends, and from the request teardown
    (``open_chat_turn``) as a backstop: when the client disconnects while the
    stream is parked at a ``yield`` (say, mid-burst of answer deltas), the
    generator is not resumed and its ``finally`` would only run whenever the
    garbage collector finalizes it, with the agent still running until then.
    """

    def __init__(
        self, res: Resources, turn: PreparedTurn, *, context_factory: ContextFactory = default_context_factory
    ) -> None:
        self._res = res
        self._turn = turn
        self._context_factory = context_factory
        self._recorder = TranscriptRecorder(turn.level)
        self._task: asyncio.Task[Any] | None = None
        self._closed = False
        self._started = time.perf_counter()
        self.turn_id = new_turn_id()

    async def events(self) -> AsyncGenerator[ChatEventModel, None]:
        turn = self._turn
        yield TurnStarted(
            turn_id=self.turn_id,
            session_id=turn.session.session_id,
            level=turn.level,
            user_message_id=turn.user_message.message_id,
        )
        finished = False
        try:
            async with aclosing(self._agent_events()) as agent_events:
                async for event in agent_events:
                    self._recorder.observe(event)
                    yield event
            finished = True
        except Exception as exc:
            log.exception("chat turn failed for session %s", turn.session.session_id)
            error = error_event_for(exc)
            self._recorder.observe(error)
            yield error
            finished = True
        finally:
            await self.close(interrupted=not finished)
        yield Done()

    async def close(self, *, interrupted: bool = True) -> None:
        """Stop the agent if it is still running and persist the answer. Idempotent."""
        if self._closed:
            return
        self._closed = True
        # Runs on disconnect too, inside a cancelled scope, where every await
        # would be cancelled again unless shielded.
        with anyio.CancelScope(shield=True):
            await self._stop_runner()
            await self._persist(interrupted=interrupted)

    async def _agent_events(self) -> AsyncGenerator[ChatEventModel, None]:
        runner = self._res.runner
        if runner is None:
            yield ErrorEvent(code="no_api_key", message=NO_API_KEY_MESSAGE, retryable=False)
            return

        knowledge = await self._knowledge()
        threshold = self._res.settings.known_threshold
        known_summary = sorted(
            (k.name for k in (knowledge or {}).values() if k.confidence >= threshold), key=str.casefold
        )
        ctx = self._context_factory(
            session=self._turn.session,
            user=self._turn.user,
            paper=self._turn.paper,
            question=self._turn.request.message,
            quote=self._turn.request.quote,
            level=self._turn.level,
            known_summary=known_summary,
            knowledge=knowledge,
            turn_id=self.turn_id,
            user_message_id=self._turn.user_message.message_id,
        )
        queue: asyncio.Queue[Any] = asyncio.Queue()
        task = asyncio.create_task(
            runner.run_turn(ctx, queue.put), name=f"turn:{self._turn.session.session_id}"
        )
        self._task = task
        # Enqueued after everything the runner emitted, so draining to _END
        # means the runner has finished and all of its events were seen.
        task.add_done_callback(lambda _: queue.put_nowait(_END))

        while (item := await queue.get()) is not _END:
            if item.type in _FRAMING:
                continue
            yield item
        if task.cancelled():
            raise RuntimeError("the agent task was cancelled")
        if (exc := task.exception()) is not None:
            raise exc

    async def _knowledge(self) -> dict[str, KnownConcept] | None:
        """The reader's KNOWS edges, or None if the graph cannot be read right now.

        A turn without the reader's history is still worth answering: the
        agent treats everything as unknown, and the runner retries the read
        for the mastery pass.
        """
        if not self._turn.graph_available:
            return None
        try:
            return await read_knowledge(self._res.graph, self._turn.user.user_id)
        except Exception:
            log.warning(
                "could not read %s's knowledge; answering without it", self._turn.user.user_id, exc_info=True
            )
            return None

    async def _stop_runner(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        with anyio.move_on_after(RUNNER_CANCEL_TIMEOUT_S):
            await asyncio.wait([task])

    async def _persist(self, *, interrupted: bool) -> None:
        elapsed_ms = round((time.perf_counter() - self._started) * 1000)
        reply = self._recorder.message(interrupted=interrupted, elapsed_ms=elapsed_ms)

        def append_answer(current: Session) -> Session:
            return current.model_copy(update={"messages": [*current.messages, reply]})

        try:
            with anyio.fail_after(PERSIST_TIMEOUT_S):
                await self._res.sessions.update(self._turn.session.session_id, append_answer)
        except Exception:
            # The reader already has the answer on screen; losing it from the
            # transcript is worth a log line, not an error in the stream.
            log.warning(
                "could not persist the answer for session %s", self._turn.session.session_id, exc_info=True
            )


async def open_chat_turn(
    turn: Annotated[PreparedTurn, Depends(prepare_turn)], res: ResourcesDep
) -> AsyncIterator[ChatTurn]:
    """Request-scoped ChatTurn whose teardown is the deterministic cleanup point (see ``ChatTurn``)."""
    chat = ChatTurn(res, turn)
    try:
        yield chat
    finally:
        await chat.close(interrupted=True)
