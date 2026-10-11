"""Session state (Redis, 24h TTL) and the user profile (Neo4j, permanent).

The split is by lifetime, applied consistently: if losing it after 24 hours is
acceptable it lives in the session; if it is something the reader *learned* or
chose for themselves, it lives in the graph.

    session:{session_id} -> Session JSON   (paper, scroll position, transcript)
    (:User {id})                            (level, onboarding)
    (:User)-[:KNOWS]->(:Concept)            (mastery -- see papermind/graph)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from papermind.schemas.events import ExternalRef, GapsFound, SourceRef
from papermind.schemas.tools import Level


class Quote(BaseModel):
    """Text the reader selected in the PDF and asked about."""

    text: str = Field(min_length=1, max_length=4000)
    page: int | None = Field(None, ge=0)


class ScrollPosition(BaseModel):
    page: int = Field(0, ge=0)
    offset: float = Field(0.0, ge=0.0, le=1.0, description="fraction down the page")
    zoom: float | None = Field(None, gt=0)


class ActivityItem(BaseModel):
    """One line of the agent's trail, persisted so a reloaded transcript can show it."""

    tool: str
    label: str
    summary: str | None = None
    ok: bool = True
    duration_ms: int | None = None


class ChatMessage(BaseModel):
    message_id: str
    role: Literal["user", "assistant"]
    text: str
    level: Level | None = None
    quote: Quote | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    external: list[ExternalRef] = Field(default_factory=list)
    gaps: GapsFound | None = None
    activity: list[ActivityItem] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    latency_ms: int | None = Field(None, description="total turn time, shown at the end of the trail summary")
    error: str | None = None
    error_code: str | None = None
    created_at: datetime


class Session(BaseModel):
    session_id: str
    user_id: str
    paper_id: str
    level: Level
    scroll: ScrollPosition = Field(default_factory=ScrollPosition)
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class UserProfile(BaseModel):
    user_id: str
    display_name: str | None = None
    level: Level = "intermediate"
    onboarded: bool = False
    created_at: datetime


class KnownConcept(BaseModel):
    concept_id: str
    name: str
    area: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["self_reported", "inferred", "onboarding"]
    evidence: str = ""
    updated_at: datetime


class ConceptInfo(BaseModel):
    concept_id: str
    name: str
    area: str | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list, description="direct prerequisite concept_ids")
