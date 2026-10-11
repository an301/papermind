"""Ingestion: arXiv id or uploaded PDF -> PaperRecord on disk, with progress.

Stages, each reported through ``on_progress(stage, fraction)``:

  DOWNLOADING  arXiv metadata (resolving the version) and the pinned PDF
  EXTRACTING   PyMuPDF -> typography-annotated lines        (thread)
  SECTIONING   lines -> outline                             (thread)
  CHUNKING     sections -> chunks with stable ids           (thread)
  INDEXING     embed every chunk window into the cache      (thread, the slow part)
  DONE

paper.json is written *after* indexing. The library lists whatever the store
holds, so writing it earlier would show a paper as ready while its vectors
are still being computed, and the first question would then embed the whole
paper inside the retriever's call timeout.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from papermind.config import get_settings
from papermind.ingest.arxiv import ArxivClient, ArxivMetadata, looks_like_pdf, parse_arxiv_id
from papermind.ingest.chunker import Vocabulary, chunk_sections, join_lines
from papermind.ingest.extract import ExtractedDocument, Line, NotAPDF, NoTextPDF, PDFTooLarge, extract_pdf
from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.sections import FRONT_MATTER_TITLE, SectionText, detect_sections
from papermind.ingest.store import PaperStore
from papermind.retrieval.dense import Embedder, EmbeddingCache, default_cache, get_embedder, load_or_embed
from papermind.retrieval.text import indexable_text
from papermind.schemas.paper import IngestStage, PaperMeta, PaperRecord

log = logging.getLogger(__name__)

ProgressFn = Callable[[IngestStage, float], Awaitable[None]]


async def _no_progress(_stage: IngestStage, _fraction: float) -> None:
    return None


def arxiv_paper_id(arxiv_id: str) -> str:
    # Old-style ids carry a slash (hep-th/9901001); a paper id is a directory
    # name and a URL segment. Same rule as the API's papers route.
    return arxiv_id.replace("/", "_")


def upload_paper_id(pdf: bytes) -> str:
    # Content-addressed: the same file uploaded twice is the same paper.
    return "up_" + hashlib.sha256(pdf).hexdigest()[:12]


def validate_pdf_bytes(pdf: bytes, *, max_bytes: int) -> None:
    """Cheap checks before PyMuPDF sees the file; messages are shown to the reader."""
    if not pdf:
        raise NotAPDF("The uploaded file is empty")
    if len(pdf) > max_bytes:
        raise PDFTooLarge(f"This PDF is larger than {max_bytes // (1024 * 1024)} MB")
    if not looks_like_pdf(pdf):
        raise NotAPDF("This file is not a PDF")


# ---------------------------------------------------------------- upload metadata

_JUNK_TITLE = re.compile(
    r"^(?:untitled|title|document|paper|main|arxiv|microsoft word\b.*|.*\.(?:pdf|dvi|tex|docx?|ps))$",
    re.IGNORECASE,
)
_NAME = re.compile(r"^[A-Z][\w'’.\-]*(?: (?:[A-Z][\w'’.\-]*|de|van|von|der|da|di|le|la)){1,4}$")
_MARKS = re.compile(r"\^\{[^}]*\}|\^\S+|[∗*†‡§¶]+|\d+$")
# Affiliation rows are capitalised word runs too ('Stanford University',
# 'Google Brain'); a name never contains one of these words.
_AFFILIATION = re.compile(
    r"\b(?:universit\w*|institut\w*|college|school|department|dept|laborator\w*|labs?|research|"
    r"inc|corp\w*|company|cent(?:er|re)|foundation|academy|hospital|google|microsoft|facebook|"
    r"openai|deepmind|biohub|team|group)\b",
    re.IGNORECASE,
)


def sensible_title(title: str | None) -> str | None:
    if not title:
        return None
    # Metadata titles copied from the typeset title keep its line-break hyphens ('Lan- guage').
    title = re.sub(r"(?<=[a-z])- (?=[a-z])", "", " ".join(title.split()))
    words = re.findall(r"[^\W\d_]{2,}", title)
    if not 4 <= len(title) <= 300 or len(words) < 2 or _JUNK_TITLE.match(title) or "\\" in title:
        return None
    return title


def title_from_layout(document: ExtractedDocument) -> str | None:
    """The largest text in the top half of page 1, joined across its rows."""
    if not document.lines:
        return None
    height = document.page_sizes[0][1] if document.page_sizes else 792.0
    candidates = [
        line
        for line in document.lines
        if line.page == 0
        and line.kind == "body"
        and line.y0 < 0.5 * height
        and sum(ch.isalpha() for ch in line.text) >= 3
    ]
    if not candidates:
        return None
    biggest = max(line.size for line in candidates)
    if biggest < document.body_size + 1.0:
        return None
    rows = [line for line in candidates if line.size >= biggest - 0.5]
    rows.sort(key=lambda line: (line.y0, line.x0))
    title = [rows[0]]
    for line in rows[1:]:
        # Consecutive rows of the title block, not a second large element further down.
        if line.y0 - title[-1].y1 > 1.2 * biggest:
            break
        title.append(line)
    return sensible_title(" ".join(line.text for line in title))


def _name_runs(part: str) -> list[str]:
    """Names set side by side on one row ('Kaiming He Xiangyu Zhang') extract as one run.

    With no initials or particles to anchor a split, an even run of four or
    more capitalised words is most often given-name/surname pairs.
    """
    words = part.split()
    if (
        len(words) >= 4
        and len(words) % 2 == 0
        and all(w[:1].isupper() and not w.endswith(".") for w in words)
    ):
        return [" ".join(words[i : i + 2]) for i in range(0, len(words), 2)]
    return [part]


def _split_names(text: str) -> list[str]:
    text = _MARKS.sub(" ", text)
    names: list[str] = []
    for chunk in re.split(r",|;| and |\s{2,}|&", text):
        for part in _name_runs(" ".join(chunk.split()).strip(" .")):
            if part and _NAME.match(part) and len(part) <= 60 and not _AFFILIATION.search(part):
                names.append(part)
    return names


def authors_from_metadata(author: str | None) -> list[str]:
    if not author or "@" in author:
        return []
    return _split_names(author)


def authors_from_layout(front: SectionText | None, title: str) -> list[str]:
    """Best effort: name-shaped pieces of the rows after the title on page 1."""
    if front is None:
        return []
    title_key = re.sub(r"\W+", "", title.lower())
    names: list[str] = []
    for line in front.lines[:12]:
        if line.page != 0 or "@" in line.text:
            continue
        if re.sub(r"\W+", "", line.text.lower()) in title_key:
            continue
        for name in _split_names(line.text):
            if name not in names:
                names.append(name)
    return names[:20]


def abstract_from_sections(sections: Sequence[SectionText]) -> str | None:
    for text in sections:
        if text.section.section_id == "sec_abstract":
            body: list[Line] = [line for line in text.lines if line.kind == "body"]
            joined, _ = join_lines([line.text for line in body], Vocabulary(body))
            return joined[:3000] or None
    return None


# ---------------------------------------------------------------- the pipeline


@dataclass
class _Source:
    paper_id: str
    source: str
    title: str | None = None
    authors: list[str] | None = None
    abstract: str | None = None
    arxiv: ArxivMetadata | None = None
    filename: str | None = None


class Ingestor:
    """Ingestion with its collaborators injected (tests pass fakes; the app uses ``default_ingestor``)."""

    def __init__(
        self,
        store: PaperStore,
        *,
        embedder: Embedder | None,
        cache: EmbeddingCache | None,
        arxiv_client: Callable[[], ArxivClient] = ArxivClient,
        max_chunk_tokens: int | None = None,
        max_upload_bytes: int | None = None,
    ) -> None:
        settings = get_settings()
        self.store = store
        self.embedder = embedder
        self.cache = cache
        self._arxiv_client = arxiv_client
        self.max_chunk_tokens = max_chunk_tokens or settings.max_chunk_tokens
        self.max_upload_bytes = max_upload_bytes or settings.max_upload_mb * 1024 * 1024

    async def ingest_arxiv(self, raw: str, *, on_progress: ProgressFn | None = None) -> PaperRecord:
        report = on_progress or _no_progress
        ref = parse_arxiv_id(raw)
        await report(IngestStage.DOWNLOADING, 0.0)
        async with self._arxiv_client() as client:
            meta = await client.metadata(ref)
            await report(IngestStage.DOWNLOADING, 0.2)
            pdf = await client.download_pdf(meta.arxiv_id, meta.version)
        await report(IngestStage.DOWNLOADING, 1.0)
        source = _Source(
            paper_id=arxiv_paper_id(meta.arxiv_id),
            source="arxiv",
            title=meta.title,
            authors=meta.authors,
            abstract=meta.abstract,
            arxiv=meta,
        )
        return await self._process(pdf, source, report)

    async def ingest_upload(
        self, pdf: bytes, filename: str, *, on_progress: ProgressFn | None = None
    ) -> PaperRecord:
        report = on_progress or _no_progress
        validate_pdf_bytes(pdf, max_bytes=self.max_upload_bytes)
        await report(IngestStage.DOWNLOADING, 1.0)
        source = _Source(paper_id=upload_paper_id(pdf), source="upload", filename=Path(filename).name or None)
        return await self._process(pdf, source, report)

    async def _process(self, pdf: bytes, source: _Source, report: ProgressFn) -> PaperRecord:
        paper_id = source.paper_id
        await report(IngestStage.EXTRACTING, 0.0)
        document = await asyncio.to_thread(extract_pdf, pdf)
        await report(IngestStage.EXTRACTING, 1.0)

        await report(IngestStage.SECTIONING, 0.0)
        sections = await asyncio.to_thread(
            detect_sections, document.lines, document.body_size, document.outline
        )
        await report(IngestStage.SECTIONING, 1.0)

        await report(IngestStage.CHUNKING, 0.0)
        chunks = await asyncio.to_thread(chunk_sections, paper_id, sections, self.max_chunk_tokens)
        if not chunks:
            raise NoTextPDF("This PDF has no selectable text that could be split into passages")
        await report(IngestStage.CHUNKING, 1.0)

        meta = self._meta(source, document, sections, len(chunks))
        record = PaperRecord(meta=meta, chunks=chunks)

        await report(IngestStage.INDEXING, 0.0)
        if self.embedder is not None:

            async def indexing(fraction: float) -> None:
                # The last few percent are the write; never report 1.0 before it.
                await report(IngestStage.INDEXING, 0.95 * fraction)

            texts = [indexable_text(chunk) for chunk in chunks]
            await load_or_embed(self.embedder, self.cache, paper_id, texts, on_progress=indexing)
        await asyncio.to_thread(self.store.save, record, pdf)
        await report(IngestStage.INDEXING, 1.0)
        await report(IngestStage.DONE, 1.0)
        log.info(
            "ingested %s: %d pages, %d sections, %d chunks",
            paper_id,
            meta.num_pages,
            len(meta.sections),
            len(chunks),
        )
        return record

    def _meta(
        self, source: _Source, document: ExtractedDocument, sections: list[SectionText], chunk_count: int
    ) -> PaperMeta:
        front = next((s for s in sections if s.section.title == FRONT_MATTER_TITLE), None)
        title = source.title or sensible_title(document.metadata.get("title")) or title_from_layout(document)
        if not title:
            title = Path(source.filename or source.paper_id).stem.replace("_", " ") or source.paper_id
        authors = source.authors
        if authors is None:
            authors = authors_from_metadata(document.metadata.get("author")) or authors_from_layout(
                front, title
            )
        abstract = source.abstract or abstract_from_sections(sections)
        arxiv = source.arxiv
        return PaperMeta(
            paper_id=source.paper_id,
            source="arxiv" if source.source == "arxiv" else "upload",
            arxiv_id=arxiv.arxiv_id if arxiv else None,
            arxiv_version=arxiv.version if arxiv else None,
            title=title,
            authors=authors,
            abstract=abstract,
            published=arxiv.published if arxiv else None,
            filename=source.filename,
            num_pages=document.num_pages,
            page_sizes=[(round(w, 2), round(h, 2)) for w, h in document.page_sizes],
            sections=[text.section for text in sections],
            chunk_count=chunk_count,
            chunker_version=CHUNKER_VERSION,
            ingested_at=datetime.now(UTC),
        )


@lru_cache(maxsize=1)
def default_ingestor() -> Ingestor:
    settings = get_settings()
    return Ingestor(PaperStore(settings.papers_dir), embedder=get_embedder(), cache=default_cache())


async def ingest_arxiv(raw: str, *, on_progress: ProgressFn | None = None) -> PaperRecord:
    """Ingest an arXiv paper (any id or URL form) into ``settings.papers_dir``."""
    return await default_ingestor().ingest_arxiv(raw, on_progress=on_progress)


async def ingest_upload(pdf: bytes, filename: str, *, on_progress: ProgressFn | None = None) -> PaperRecord:
    """Ingest an uploaded PDF into ``settings.papers_dir`` as ``up_<sha256[:12]>``."""
    return await default_ingestor().ingest_upload(pdf, filename, on_progress=on_progress)
