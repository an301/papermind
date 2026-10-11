"""Paper, section, and chunk models -- the shape of an ingested paper.

These are pure data (no I/O). They are shared by ingestion, the retriever MCP
server, the API, and the eval harness, so they are the one definition of what
a chunk is. Two conventions matter everywhere:

* Page indexes are 0-based.
* Geometry is in PDF points with a TOP-LEFT origin (PyMuPDF's convention). The
  frontend converts to pdf.js viewport space; nothing on the server ever flips y.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Box(BaseModel):
    """A rectangle on one page, in PDF points, origin top-left."""

    page: int = Field(ge=0)
    x0: float
    y0: float
    x1: float
    y1: float


class Section(BaseModel):
    """One heading in the paper's outline."""

    section_id: str = Field(description="e.g. 'sec_3.2' or 'sec_abstract'; unique within the paper")
    number: str | None = Field(None, description="'3.2', 'A.1', or None for unnumbered sections")
    title: str
    level: int = Field(1, ge=1, description="1 = top-level section")
    page: int = Field(ge=0, description="0-based page the heading is on")
    y: float = Field(0.0, description="heading top in points from the page top, for scroll-to")


class Chunk(BaseModel):
    """A retrievable unit of text. Its id doubles as an eval label, so it must be stable.

    chunk_id format: "{paper_id}:sec_{number}_{slug}" with an optional "_p{n}"
    suffix when a section is split, e.g. "1706.03762:sec_3.2.1_scaled_dot_product_attention"
    or "1706.03762:sec_references_p2". Never derived from list position.
    """

    chunk_id: str
    paper_id: str
    section_id: str
    section_number: str | None = None
    section_title: str
    part: int | None = Field(None, description="1-based part index when the section was split")
    text: str
    token_count: int = Field(ge=0)
    page_start: int = Field(ge=0)
    page_end: int = Field(ge=0)
    boxes: list[Box] = Field(default_factory=list, description="block-level rects covering the chunk")


class PaperStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class IngestStage(StrEnum):
    DOWNLOADING = "downloading"
    EXTRACTING = "extracting"
    SECTIONING = "sectioning"
    CHUNKING = "chunking"
    INDEXING = "indexing"
    DONE = "done"


class PaperMeta(BaseModel):
    """Everything about a paper except its chunks."""

    paper_id: str = Field(description="arXiv id without version ('1706.03762') or 'up_<sha12>' for uploads")
    source: Literal["arxiv", "upload"]
    arxiv_id: str | None = None
    arxiv_version: str | None = Field(None, description="resolved version, e.g. 'v7'")
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str | None = None
    published: date | None = None
    filename: str | None = Field(None, description="original filename for uploads")
    num_pages: int = Field(ge=0)
    page_sizes: list[tuple[float, float]] = Field(default_factory=list, description="(width, height) in points per page")
    sections: list[Section] = Field(default_factory=list)
    chunk_count: int = Field(0, ge=0)
    chunker_version: str
    ingested_at: datetime


class PaperRecord(BaseModel):
    """What ingestion writes to disk: data/papers/{paper_id}/paper.json."""

    meta: PaperMeta
    chunks: list[Chunk]


class StageDetail(BaseModel):
    """Counts behind the progress bar, e.g. pages read or passages indexed."""

    done: int = Field(ge=0)
    total: int = Field(ge=0)


class PaperSummary(BaseModel):
    """A row in the library, including in-flight ingestions."""

    paper_id: str
    source: Literal["arxiv", "upload"]
    title: str
    authors: list[str] = Field(default_factory=list)
    published: date | None = None
    num_pages: int = 0
    status: PaperStatus
    stage: IngestStage | None = None
    progress: float = Field(0.0, ge=0.0, le=1.0)
    stage_detail: StageDetail | None = None
    error: str | None = None
    error_code: str | None = Field(None, description="machine-readable failure reason, see AddPaperErrorCode")
    added_at: datetime
