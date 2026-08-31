"""Data models for the ingestion pipeline. No I/O, no side effects.

`Chunk` is the unit that lands in JSON and that eval labels point at. Its field
set is a contract: adding a field is safe, renaming or removing one breaks
every stored benchmark answer.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Line(BaseModel):
    """One visual line of text, with the typography needed to spot headings."""

    text: str
    size: float          # max span size on the line, in points
    bold: bool
    page: int            # 1-indexed, matching what the reader sees
    y: float             # top coordinate, for header/footer detection
    x0: float = 0.0      # left edge, for indentation-based paragraph breaks
    column: int = 0      # 0 = left / single column, 1 = right
    furniture: bool = False  # figure/table caption or in-figure label


class Section:
    """A contiguous run of body text under one heading.

    Plain class rather than a model: it is a build-time intermediate that holds
    mutable line lists, and is never serialised.
    """

    def __init__(
        self,
        number: str | None,
        title: str,
        page_start: int,
        level: int = 1,
    ) -> None:
        self.number = number
        self.title = title
        self.page_start = page_start
        self.page_end = page_start
        self.level = level
        self.lines: list[Line] = []
        # Captions and in-figure labels, held apart from the prose flow and
        # appended after it -- see Section.add.
        self.furniture: list[Line] = []

    def add(self, line: Line) -> None:
        target = self.furniture if line.furniture else self.lines
        target.append(line)
        self.page_end = max(self.page_end, line.page)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        label = f"{self.number} {self.title}" if self.number else self.title
        return f"<Section {label!r} pp.{self.page_start}-{self.page_end} lines={len(self.lines)}>"


class Chunk(BaseModel):
    """One stored, retrievable unit of a paper.

    `id` is the eval label -- see `papermind/ingest/ids.py` for its stability
    rules. Everything else is descriptive.
    """

    id: str
    paper_id: str
    section_number: str | None = None
    section_title: str
    text: str
    token_count: int
    token_counter: str = "estimate"      # "estimate" | "anthropic"
    char_count: int
    part: int = 1
    n_parts: int = 1
    page_start: int
    page_end: int


class ArxivEntry(BaseModel):
    """What the arXiv Atom API reports, before the PDF is fetched.

    A model rather than a dict so the resolved version is a typed `int` all the
    way to `build_meta` -- this is the value that pins reproducibility, and it
    should not be reachable only through a cast.
    """

    version: int
    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    published: str | None = None
    updated: str | None = None
    primary_category: str | None = None


class PaperMeta(BaseModel):
    """arXiv metadata plus the provenance needed to reproduce an ingest."""

    paper_id: str                         # canonical, unversioned: "1706.03762"
    versioned_id: str                     # exactly what was fetched: "1706.03762v7"
    version: int
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    categories: list[str] = Field(default_factory=list)
    published: str | None = None
    updated: str | None = None
    pdf_sha256: str                       # changes if arXiv reissues the PDF
    pdf_url: str
    n_pages: int


class IngestResult(BaseModel):
    """The on-disk document: `data/papers/{paper_id}.json`."""

    schema_version: int = 1
    chunker_version: int
    ingested_at: str
    meta: PaperMeta
    max_chunk_tokens: int
    chunks: list[Chunk]

    @property
    def n_chunks(self) -> int:
        return len(self.chunks)
