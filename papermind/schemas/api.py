"""HTTP request/response bodies. Mirrored in frontend/src/api/types.ts.

Endpoint map (all under /api):

    GET    /health                                  -> HealthResponse
    GET    /papers                                  -> list[PaperSummary]
    POST   /papers            AddArxivPaperRequest  -> 202 PaperSummary
    POST   /papers/upload     multipart 'file'      -> 202 PaperSummary
    GET    /papers/{paper_id}                       -> PaperDetail
    GET    /papers/{paper_id}/pdf                   -> application/pdf
    GET    /papers/{paper_id}/chunks/{chunk_id}     -> ChunkDetail
    GET    /papers/{paper_id}/suggestions?level=    -> SuggestionsResponse
    DELETE /papers/{paper_id}                       -> 204

    POST   /users                                   -> UserProfile (anonymous user)
    GET    /users/{user_id}                         -> UserProfile
    PATCH  /users/{user_id}   UpdateUserRequest     -> UserProfile
    GET    /users/{user_id}/knowledge               -> list[KnownConcept]
    POST   /users/{user_id}/knowledge  AddKnowledgeRequest     -> KnownConcept
    POST   /users/{user_id}/knowledge/areas  OnboardingAreasRequest -> list[KnownConcept]
    DELETE /users/{user_id}/knowledge/{concept_id}  -> 204

    GET    /concepts?q=&limit=                      -> list[ConceptInfo]
    GET    /concepts/areas                          -> list[ConceptArea]
    GET    /concepts/{concept_id}?user_id=          -> ConceptDetail

    POST   /sessions          CreateSessionRequest  -> Session
    GET    /sessions/{session_id}                   -> Session
    PATCH  /sessions/{session_id} UpdateSessionRequest -> Session

    POST   /chat              ChatRequest           -> text/event-stream of ChatEvent
    POST   /feedback          FeedbackRequest       -> 204
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from papermind.schemas.paper import Box, PaperMeta, PaperSummary
from papermind.schemas.session import ConceptInfo, Quote, ScrollPosition
from papermind.schemas.tools import ConceptRef, Gap, Level


class ServiceCheck(BaseModel):
    ok: bool
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    checks: dict[str, ServiceCheck] = Field(
        description="redis, neo4j, mcp:retriever, mcp:prereq_graph, mcp:semantic_scholar, anthropic_key"
    )
    access_code_required: bool = Field(
        default=False, description="every route but /health needs the X-Access-Code header"
    )
    paper_delete_allowed: bool = Field(
        default=True, description="false on a shared public server: papers cannot be removed"
    )


AddPaperErrorCode = Literal[
    "invalid_input",
    "not_found",
    "withdrawn",
    "newer_version",
    "already_added",
    "arxiv_unreachable",
    "not_a_pdf",
    "too_large",
    "encrypted",
    "no_text",
    "ingest_failed",
]


class AddPaperError(BaseModel):
    """Body of {"error": ...} for add-paper failures, and the shape behind PaperSummary.error_code."""

    code: AddPaperErrorCode
    message: str
    latest_version: str | None = None
    paper_id: str | None = None


class FeedbackRequest(BaseModel):
    message_id: str
    session_id: str
    reason: Literal["wrong", "unclear", "wrong_level", "bad_citation", "other"]
    note: str = Field("", max_length=2000)


class AddArxivPaperRequest(BaseModel):
    arxiv: str = Field(min_length=4, max_length=300, description="arXiv id or abs/pdf URL, versioned or not")


class PaperDetail(BaseModel):
    summary: PaperSummary
    meta: PaperMeta | None = Field(None, description="present once status == ready")


class ChunkDetail(BaseModel):
    chunk_id: str
    paper_id: str
    section_id: str
    section_number: str | None = None
    section_title: str
    text: str
    page_start: int
    page_end: int
    boxes: list[Box]


class SuggestionsResponse(BaseModel):
    paper_id: str
    level: Level
    questions: list[str]


class UpdateUserRequest(BaseModel):
    display_name: str | None = Field(None, max_length=60)
    level: Level | None = None
    onboarded: bool | None = None


class AddKnowledgeRequest(BaseModel):
    concept: str = Field(min_length=1, max_length=120, description="concept id or name")


class OnboardingAreasRequest(BaseModel):
    areas: list[str] = Field(description="ConceptArea.area_id values the reader says they are comfortable with")


class ConceptArea(BaseModel):
    area_id: str
    name: str
    description: str
    concept_count: int


class ConceptDetail(BaseModel):
    concept: ConceptInfo
    prerequisites: list[ConceptRef]
    known: bool = False
    learning_path: list[Gap] = Field(default_factory=list, description="for the given user, prerequisites first")


class CreateSessionRequest(BaseModel):
    user_id: str
    paper_id: str
    level: Level | None = Field(None, description="defaults to the user's profile level")


class UpdateSessionRequest(BaseModel):
    scroll: ScrollPosition | None = None
    level: Level | None = None


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=4000)
    quote: Quote | None = None
    level: Level | None = Field(None, description="override for this turn; also updates the session level")
