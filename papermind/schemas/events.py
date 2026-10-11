"""Typed events streamed to the browser over one SSE channel per turn.

Wire format, one frame per event:

    event: <type>
    data: <json of the model, including "type">

The frontend mirrors these in ``frontend/src/api/types.ts``. Change both together.

Order within a turn (tool events interleave as the agent works):

    turn.started
    (tool.started | tool.retry | tool.finished | gaps)*
    sources
    answer.delta*
    answer.done
    knowledge.updated?        -- after the mastery pass, may be empty and omitted
    followups?                -- up to two suggested next questions
    usage
    done

An ``error`` event may replace anything after ``turn.started``; ``done`` always ends the stream.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from papermind.schemas.tools import Altitude, ConceptRef, Gap, Level


class TurnStarted(BaseModel):
    type: Literal["turn.started"] = "turn.started"
    turn_id: str
    session_id: str
    level: Level
    user_message_id: str


class ToolStarted(BaseModel):
    type: Literal["tool.started"] = "tool.started"
    call_id: str
    tool: str
    label: str = Field(description="human sentence, e.g. 'Searching the paper for “label smoothing”'")
    args: dict = Field(default_factory=dict, description="validated model-facing args (no injected context)")


class ToolRetry(BaseModel):
    type: Literal["tool.retry"] = "tool.retry"
    call_id: str
    tool: str
    error: str = Field(description="validation error text that was returned to the model")
    attempt: int


class ToolFinished(BaseModel):
    type: Literal["tool.finished"] = "tool.finished"
    call_id: str
    tool: str
    ok: bool
    duration_ms: int
    summary: str = Field(description="human sentence, e.g. 'Found 5 passages in §3.2 and §5.4'")
    detail: dict | None = None


class GapsFound(BaseModel):
    type: Literal["gaps"] = "gaps"
    concept: ConceptRef | None
    gaps: list[Gap]
    teaching_order: list[ConceptRef] = Field(description="prerequisites first, target last")
    altitude: Altitude


class SourceRef(BaseModel):
    label: str = Field(description="marker the writer uses inline, e.g. 'S1'")
    chunk_id: str
    section_number: str | None = None
    section_title: str
    page_start: int
    page_end: int
    snippet: str = Field(description="first ~240 characters, for hover previews")
    cited_sentence: str | None = Field(
        None, description="the sentence of the chunk the answer relied on most, for bolding in passage cards"
    )


class SourcesReady(BaseModel):
    type: Literal["sources"] = "sources"
    sources: list[SourceRef]


class AnswerDelta(BaseModel):
    type: Literal["answer.delta"] = "answer.delta"
    text: str


class ExternalRef(BaseModel):
    """A Semantic Scholar paper the writer cited inline as [P1], [P2] ..."""

    label: str = Field(description="marker the writer uses inline, e.g. 'P1'")
    s2_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    tldr: str | None = None
    arxiv_id: str | None = None
    url: str | None = None


class AnswerDone(BaseModel):
    type: Literal["answer.done"] = "answer.done"
    message_id: str
    text: str = Field(description="full markdown; paper passages cited as [S1], external papers as [P1]")
    cited: list[str] = Field(default_factory=list, description="source labels actually used in the text")
    external: list[ExternalRef] = Field(default_factory=list, description="external papers cited as [P#]")
    sources: list[SourceRef] = Field(
        default_factory=list,
        description="the final sources, same labels as the 'sources' event, with cited_sentence filled in",
    )
    writer: str = Field(description="which writer produced the text, e.g. 'claude:claude-opus-5-5'")


class KnowledgeUpdated(BaseModel):
    type: Literal["knowledge.updated"] = "knowledge.updated"
    added: list[ConceptRef] = Field(default_factory=list)
    removed: list[ConceptRef] = Field(default_factory=list)
    ask: list[ConceptRef] = Field(
        default_factory=list,
        description="self-reported concepts the reader now seems unsure of: the UI asks, the system never removes them",
    )


class Followups(BaseModel):
    """Up to two follow-up questions grounded in this answer and its gaps (after answer.done)."""

    type: Literal["followups"] = "followups"
    message_id: str
    questions: list[str] = Field(default_factory=list, max_length=2)


class UsageReport(BaseModel):
    type: Literal["usage"] = "usage"
    model_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: float
    latency_ms: int
    ttft_ms: int | None = Field(None, description="time to first answer token")
    tool_calls: int = 0
    tool_retries: int = 0


ErrorCode = Literal[
    "no_api_key",
    "auth_failed",
    "rate_limited",
    "overloaded",
    "api_error",
    "refusal",
    "paper_not_ready",
    "session_not_found",
    "tool_failed",
    "internal",
]


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: ErrorCode
    message: str = Field(description="plain-language explanation and what to do about it")
    retryable: bool = False


class Done(BaseModel):
    type: Literal["done"] = "done"


ChatEvent = Annotated[
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
    | Done,
    Field(discriminator="type"),
]
