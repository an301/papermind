"""The ingestion pipeline end to end on synthetic PDFs: no network, no model."""

from __future__ import annotations

from datetime import date
from types import TracebackType

import pymupdf
import pytest

from papermind.ingest.arxiv import ArxivMetadata, ArxivNotFound, ArxivRef
from papermind.ingest.extract import EncryptedPDF, NotAPDF, NoTextPDF, PDFError, PDFTooLarge
from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.pipeline import (
    Ingestor,
    arxiv_paper_id,
    authors_from_metadata,
    sensible_title,
    upload_paper_id,
)
from papermind.ingest.store import PaperStore
from papermind.retrieval.dense import EmbeddingCache
from papermind.retrieval.index import IndexRegistry
from papermind.schemas.paper import IngestStage
from tests.unit.conftest import HashEmbedder, PdfBuilder


class FakeArxiv:
    """Stands in for ArxivClient: records calls, serves one canned paper."""

    def __init__(self, pdf: bytes, *, version: str = "v7", missing: bool = False) -> None:
        self.pdf = pdf
        self.version = version
        self.missing = missing
        self.calls: list[str] = []

    def __call__(self) -> FakeArxiv:  # Ingestor takes a client factory
        return self

    async def __aenter__(self) -> FakeArxiv:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def metadata(self, ref: ArxivRef) -> ArxivMetadata:
        self.calls.append(f"metadata {ref.versioned_id}")
        if self.missing:
            raise ArxivNotFound(f"arXiv has no paper {ref.versioned_id}")
        return ArxivMetadata(
            arxiv_id=ref.arxiv_id,
            version=ref.version or self.version,
            title="Synthetic Layouts for Testing Extraction",
            authors=["Ada Lovelace", "Alan Turing"],
            abstract="We study self-attention in synthetic documents.",
            published=date(2024, 1, 1),
        )

    async def download_pdf(self, arxiv_id: str, version: str) -> bytes:
        self.calls.append(f"pdf {arxiv_id}{version}")
        return self.pdf


@pytest.fixture
def store(tmp_path) -> PaperStore:
    return PaperStore(tmp_path / "papers")


@pytest.fixture
def cache(tmp_path) -> EmbeddingCache:
    return EmbeddingCache(tmp_path / "embeddings")


def make_ingestor(store, embedder, cache, *, arxiv=None, max_upload_bytes=None) -> Ingestor:
    return Ingestor(
        store,
        embedder=embedder,
        cache=cache,
        arxiv_client=arxiv or FakeArxiv(b""),
        max_chunk_tokens=600,
        max_upload_bytes=max_upload_bytes,
    )


class Progress:
    def __init__(self) -> None:
        self.events: list[tuple[IngestStage, float]] = []

    async def __call__(self, stage: IngestStage, fraction: float) -> None:
        self.events.append((stage, fraction))


ORDER = list(IngestStage)


def assert_well_formed(progress: Progress) -> None:
    stages = [stage for stage, _ in progress.events]
    assert stages[0] == IngestStage.DOWNLOADING and progress.events[-1] == (IngestStage.DONE, 1.0)
    assert [ORDER.index(s) for s in stages] == sorted(ORDER.index(s) for s in stages)  # never goes back
    for stage in ORDER:
        fractions = [f for s, f in progress.events if s == stage]
        assert fractions == sorted(fractions) and all(0.0 <= f <= 1.0 for f in fractions), stage
        assert fractions and fractions[-1] == 1.0, stage


async def test_upload_end_to_end(store, cache, two_column_pdf: bytes) -> None:
    embedder = HashEmbedder()
    progress = Progress()
    record = await make_ingestor(store, embedder, cache).ingest_upload(
        two_column_pdf, "/tmp/some dir/My Paper.pdf", on_progress=progress
    )
    meta = record.meta
    assert meta.paper_id == upload_paper_id(two_column_pdf) and meta.paper_id.startswith("up_")
    assert len(meta.paper_id) == len("up_") + 12
    assert meta.source == "upload" and meta.arxiv_id is None and meta.filename == "My Paper.pdf"
    # No title in the PDF's metadata: the largest text on page 1 is the title.
    assert meta.title == "Synthetic Layouts for Testing Extraction"
    assert meta.authors == ["Ada Lovelace", "Alan Turing"]
    assert meta.abstract and meta.abstract.startswith("We study self-attention in synthetic documents.")
    assert meta.num_pages == 2 and meta.page_sizes == [(612.0, 792.0), (612.0, 792.0)]
    assert meta.chunker_version == CHUNKER_VERSION and meta.chunk_count == len(record.chunks)
    assert [s.section_id for s in meta.sections][:4] == ["sec_front_matter", "sec_abstract", "sec_1", "sec_2"]
    assert all(c.chunk_id.startswith(meta.paper_id + ":") for c in record.chunks)
    assert_well_formed(progress)
    # Committed to the store, PDF included.
    assert store.load(meta.paper_id) == record
    assert store.pdf_path(meta.paper_id).read_bytes() == two_column_pdf


async def test_indexing_fills_the_cache_the_retriever_reads(store, cache, two_column_pdf: bytes) -> None:
    embedder = HashEmbedder()
    record = await make_ingestor(store, embedder, cache).ingest_upload(two_column_pdf, "p.pdf")
    embedded = embedder.passages_embedded
    assert embedded >= len(record.chunks)
    registry = IndexRegistry(store, embedder, cache=cache)
    index = await registry.get(record.meta.paper_id)
    assert len(index.chunks) == len(record.chunks)
    assert embedder.passages_embedded == embedded  # loaded from the cache, nothing re-embedded


async def test_same_file_twice_is_the_same_paper(store, cache, two_column_pdf: bytes) -> None:
    ingestor = make_ingestor(store, HashEmbedder(), cache)
    first = await ingestor.ingest_upload(two_column_pdf, "a.pdf")
    second = await ingestor.ingest_upload(two_column_pdf, "b.pdf")
    assert first.meta.paper_id == second.meta.paper_id
    assert [m.paper_id for m in store.list()] == [first.meta.paper_id]


async def test_title_from_pdf_metadata_when_sensible(store, cache) -> None:
    builder = PdfBuilder()
    builder.new_page()
    builder.text(72, 80, "A Title Set In Large Type", size=18, bold=True)
    builder.paragraph(72, 120, "Body text that is long enough to count as a real paper. " * 12)
    builder.doc.set_metadata(
        {"title": "The Real Title From Metadata", "author": "Grace Hopper; Edsger Dijkstra"}
    )
    record = await make_ingestor(store, HashEmbedder(), cache).ingest_upload(builder.bytes(), "x.pdf")
    assert record.meta.title == "The Real Title From Metadata"
    assert record.meta.authors == ["Grace Hopper", "Edsger Dijkstra"]


async def test_arxiv_end_to_end_records_the_resolved_version(store, cache, two_column_pdf: bytes) -> None:
    arxiv = FakeArxiv(two_column_pdf, version="v7")
    progress = Progress()
    record = await make_ingestor(store, HashEmbedder(), cache, arxiv=arxiv).ingest_arxiv(
        "https://arxiv.org/abs/2401.00001", on_progress=progress
    )
    meta = record.meta
    assert arxiv.calls == ["metadata 2401.00001", "pdf 2401.00001v7"]
    assert (meta.paper_id, meta.source, meta.arxiv_id, meta.arxiv_version) == (
        "2401.00001",
        "arxiv",
        "2401.00001",
        "v7",
    )
    assert meta.title == "Synthetic Layouts for Testing Extraction"
    assert meta.authors == ["Ada Lovelace", "Alan Turing"]
    assert meta.published == date(2024, 1, 1)
    assert meta.abstract == "We study self-attention in synthetic documents."
    assert any(c.chunk_id == "2401.00001:sec_2.1_scaled_attention" for c in record.chunks)
    assert_well_formed(progress)
    assert store.meta("2401.00001") == meta


async def test_pinned_version_is_fetched_as_pinned(store, cache, two_column_pdf: bytes) -> None:
    arxiv = FakeArxiv(two_column_pdf)
    record = await make_ingestor(store, HashEmbedder(), cache, arxiv=arxiv).ingest_arxiv("2401.00001v2")
    assert arxiv.calls == ["metadata 2401.00001v2", "pdf 2401.00001v2"]
    assert record.meta.arxiv_version == "v2"


def test_old_style_ids_become_safe_paper_ids() -> None:
    assert arxiv_paper_id("hep-th/9901001") == "hep-th_9901001"
    assert arxiv_paper_id("1706.03762") == "1706.03762"


async def test_arxiv_errors_propagate_and_nothing_is_written(store, cache) -> None:
    ingestor = make_ingestor(store, HashEmbedder(), cache, arxiv=FakeArxiv(b"", missing=True))
    with pytest.raises(ArxivNotFound):
        await ingestor.ingest_arxiv("2401.99999")
    assert store.list() == []


def encrypted_pdf() -> bytes:
    builder = PdfBuilder()
    builder.new_page()
    builder.paragraph(72, 100, "Secret text. " * 50)
    return builder.bytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")


def scanned_pdf() -> bytes:
    builder = PdfBuilder()
    page = builder.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 550, 740), color=(0, 0, 0), fill=(0.7, 0.7, 0.7))
    return builder.bytes()


@pytest.mark.parametrize(
    ("make_pdf", "error", "message"),
    [
        (lambda: b"", NotAPDF, "empty"),
        (lambda: b"<html>not a pdf</html>", NotAPDF, "not a PDF"),
        (lambda: b"%PDF-1.7\n garbage that is not a PDF body", PDFError, "could not be read"),
        (encrypted_pdf, EncryptedPDF, "password"),
        (scanned_pdf, NoTextPDF, "This PDF has no selectable text"),
    ],
)
async def test_bad_uploads_fail_with_a_readable_reason(store, cache, make_pdf, error, message) -> None:
    with pytest.raises(error, match=message):
        await make_ingestor(store, HashEmbedder(), cache).ingest_upload(make_pdf(), "bad.pdf")
    assert store.list() == []


async def test_oversized_upload_is_refused_before_parsing(store, cache, two_column_pdf: bytes) -> None:
    ingestor = make_ingestor(store, HashEmbedder(), cache, max_upload_bytes=1024)
    with pytest.raises(PDFTooLarge, match="larger than"):
        await ingestor.ingest_upload(two_column_pdf, "big.pdf")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Attention Is All You Need", "Attention Is All You Need"),
        ("  Deep   Residual\nLearning  ", "Deep Residual Learning"),
        (
            "LoRA: Low-Rank Adaptation of Large Lan- guage Models",
            "LoRA: Low-Rank Adaptation of Large Language Models",
        ),
        ("untitled", None),
        ("Microsoft Word - draft7.docx", None),
        ("main.pdf", None),
        ("x", None),
        (None, None),
        ("\\title{Broken}", None),
    ],
)
def test_sensible_title(raw: str | None, expected: str | None) -> None:
    assert sensible_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ashish Vaswani, Noam Shazeer and Niki Parmar", ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"]),
        ("Kaiming He; Xiangyu Zhang", ["Kaiming He", "Xiangyu Zhang"]),
        # Affiliations are capitalised word runs too, but never names.
        ("Chelsea Finn, Stanford University CZ Biohub", ["Chelsea Finn"]),
        ("Aidan N. Gomez, Google Brain, University of Toronto", ["Aidan N. Gomez"]),
        ("Google Research, Brain Team", []),
        # Names set side by side on one row, no separators left in the text.
        (
            "Kaiming He Xiangyu Zhang Shaoqing Ren Jian Sun",
            ["Kaiming He", "Xiangyu Zhang", "Shaoqing Ren", "Jian Sun"],
        ),
        ("Christopher D. Manning", ["Christopher D. Manning"]),
        ("someone@example.com", []),
        ("", []),
    ],
)
def test_authors_from_metadata(raw: str, expected: list[str]) -> None:
    assert authors_from_metadata(raw) == expected
