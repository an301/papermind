"""Tool I/O contracts -- the validation boundary between the model and the tools.

The model does not call functions. It emits JSON that looks like a function
call, token by token, so it is sometimes wrong ("k": "five", a missing field, a
tool that does not exist). Every model-facing argument model below is therefore
both:

1. the JSON Schema we advertise to the model (``Model.model_json_schema()``), and
2. the runtime validator the dispatcher runs before anything reaches a tool.

Same object for both, so the advertised schema and the enforced schema cannot
drift. A ``ValidationError`` is returned to the model as an ``is_error``
tool_result so it can correct itself (see ``papermind/agent/dispatch.py``).

Field ``description``s are prompt engineering, not documentation: the model
reads them while constructing the call. Keep them specific.

Context the model must not choose -- which paper is open, which user is
reading -- is NOT a model-facing field. The dispatcher injects it from the
session when it forwards the call to the MCP server (``*ServerArgs``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Level = Literal["beginner", "intermediate", "expert"]
Altitude = Literal["technical", "scaffolded", "intuition"]
RetrievalMode = Literal["hybrid", "bm25", "dense"]


class _Args(BaseModel):
    # Unknown keys are an error, not silently dropped: a misspelled field
    # ("section" for "section_filter") should come back to the model as a
    # correction, otherwise it believes the filter was applied.
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- retriever


class SearchPaperArgs(_Args):
    """Search the paper the reader has open for the passages that answer a question."""

    query: str = Field(
        min_length=2,
        max_length=200,
        description=(
            "3-8 keywords using the technical terms the paper itself would use "
            "(e.g. 'scaled dot-product attention softmax', 'label smoothing regularization'), "
            "not conversational phrasing."
        ),
    )
    k: int = Field(
        5,
        ge=1,
        le=10,
        description="How many passages to return. 5 is right for almost every question.",
    )
    section_filter: str | None = Field(
        None,
        max_length=20,
        description="Optional section number prefix such as '3' or '3.2' to restrict the search; omit otherwise.",
    )


class SearchPaperServerArgs(SearchPaperArgs):
    paper_id: str
    mode: RetrievalMode = "hybrid"


class RetrievedChunk(BaseModel):
    chunk_id: str
    section_id: str
    section_number: str | None = None
    section_title: str
    text: str
    page_start: int
    page_end: int
    rank: int = Field(description="1-based rank after fusion")
    score: float
    bm25_rank: int | None = None
    dense_rank: int | None = None


class SearchPaperResult(BaseModel):
    paper_id: str
    query: str
    mode: RetrievalMode
    chunks: list[RetrievedChunk]


# ---------------------------------------------------------------- prerequisite graph


class ConceptRef(BaseModel):
    concept_id: str
    name: str


class Gap(BaseModel):
    concept_id: str
    name: str
    depth: int = Field(ge=0, description="BFS distance from the asked-about concept (0 = the concept itself)")
    description: str | None = None


class CheckPrerequisitesArgs(_Args):
    """Find which prerequisites of a concept this reader does not know yet."""

    concept: str = Field(
        min_length=2,
        max_length=120,
        description=(
            "The single ML concept the question hinges on, as its canonical name "
            "(e.g. 'scaled dot-product attention', 'batch normalization', 'LoRA'). One concept, not a sentence."
        ),
    )


class CheckPrerequisitesServerArgs(CheckPrerequisitesArgs):
    user_id: str


class CheckPrerequisitesResult(BaseModel):
    query: str
    matched: ConceptRef | None = Field(None, description="graph node the query resolved to, if any")
    match_score: float | None = None
    suggestions: list[ConceptRef] = Field(default_factory=list, description="near matches when nothing matched")
    gaps: list[Gap] = Field(default_factory=list, description="unknown concepts in BFS order from the target")
    known_stops: list[ConceptRef] = Field(
        default_factory=list, description="known concepts where the walk stopped (foundations are covered)"
    )
    teaching_order: list[ConceptRef] = Field(
        default_factory=list, description="the gaps in the order to teach them: prerequisites first, target last"
    )
    altitude: Altitude = Field(description="technical: 0-2 gaps, scaffolded: 3-10, intuition: more than 10")


class LearningPathArgs(_Args):
    """Prerequisites this reader is missing for a concept, in the order to teach them."""

    concept: str = Field(
        min_length=2,
        max_length=120,
        description="The concept to build up to, as its canonical ML name.",
    )


class LearningPathServerArgs(LearningPathArgs):
    user_id: str


class LearningPathResult(BaseModel):
    query: str
    matched: ConceptRef | None = None
    steps: list[Gap] = Field(default_factory=list, description="prerequisites first, the target concept last")


class MarkKnownServerArgs(_Args):
    """Server-side only: written by the mastery pass and the knowledge API, never by the agent loop."""

    user_id: str
    concept: str = Field(min_length=1, max_length=120, description="concept id or name")
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["self_reported", "inferred", "onboarding"]
    evidence: str = Field("", max_length=500)


class ForgetConceptServerArgs(_Args):
    """Server-side only: remove a KNOWS edge (reader said they don't know it, or undid an inference)."""

    user_id: str
    concept: str = Field(min_length=1, max_length=120, description="concept id or name")


class MarkKnownResult(BaseModel):
    concept: ConceptRef | None
    status: Literal["added", "updated", "removed", "not_found"]
    confidence: float | None = None


# ---------------------------------------------------------------- semantic scholar


class ScholarPaper(BaseModel):
    s2_id: str
    title: str
    year: int | None = None
    authors: list[str] = Field(default_factory=list, description="first few authors")
    venue: str | None = None
    abstract: str | None = None
    tldr: str | None = None
    citation_count: int | None = None
    url: str | None = None
    arxiv_id: str | None = None


class LookupPaperArgs(_Args):
    """Look up a paper that is NOT the one being read (a cited method, a dataset paper, a metric)."""

    query: str = Field(
        min_length=2,
        max_length=300,
        description="A paper title, an arXiv id like '1409.0473', or a DOI. Prefer the exact title when the paper cites it.",
    )
    limit: int = Field(3, ge=1, le=5, description="How many candidate matches to return.")


class LookupPaperResult(BaseModel):
    query: str
    matches: list[ScholarPaper]


class PaperGraphArgs(_Args):
    """References of a paper (what it cites) or citations (what cites it)."""

    paper: str | None = Field(
        None,
        max_length=300,
        description="Title, arXiv id, or Semantic Scholar id. Omit to mean the paper the reader has open.",
    )
    limit: int = Field(10, ge=1, le=25, description="How many papers to return.")


class PaperGraphServerArgs(PaperGraphArgs):
    # Filled in by the dispatcher when the model omitted `paper`.
    default_paper: str | None = None


class PaperGraphResult(BaseModel):
    paper: str
    resolved: ScholarPaper | None = None
    papers: list[ScholarPaper] = Field(default_factory=list)
    total: int | None = None


# ---------------------------------------------------------------- hand-off to the writer


class ComposeAnswerArgs(_Args):
    """Agent-local terminal tool: research is done, hand the plan to the writer.

    Not an MCP tool. Calling it ends the ReAct loop; the writer model then
    produces the explanation the reader sees.
    """

    plan: str = Field(
        min_length=10,
        max_length=1500,
        description=(
            "2-5 sentences for the writer: what the answer must cover, in what order, "
            "and at what altitude for this reader. Mention which prerequisites to build first."
        ),
    )
    source_chunk_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="chunk_ids from search_paper results the answer should rest on, most important first.",
    )
    concepts_to_scaffold: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Prerequisite concepts to explain briefly before the main idea, in teaching order.",
    )
    external_papers: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="s2_id values of looked-up papers worth mentioning, if any.",
    )
