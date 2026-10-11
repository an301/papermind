"""Live arXiv: version resolution, the pinned PDF download, and a full ingest.

A handful of requests on purpose: arXiv asks for one request every three
seconds, and the module-wide schedule enforces it, so this file takes ~20s.
The embedding model is replaced by the unit tests' hash embedder; what is
under test here is arXiv and the PDF pipeline, not the encoder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from papermind.ingest.arxiv import ArxivClient, ArxivNotFound, parse_arxiv_id
from papermind.ingest.extract import extract_pdf
from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.pipeline import Ingestor
from papermind.ingest.store import PaperStore
from papermind.retrieval.dense import EmbeddingCache
from tests.unit.conftest import HashEmbedder

pytestmark = pytest.mark.integration

TRANSFORMER = "1706.03762"


async def test_metadata_resolves_the_latest_version_and_reads_the_entry() -> None:
    async with ArxivClient() as client:
        meta = await client.metadata(parse_arxiv_id(f"https://arxiv.org/abs/{TRANSFORMER}"))
    assert meta.arxiv_id == TRANSFORMER
    # v7 is the latest at the time of writing; a newer one must still be a version.
    assert meta.version.startswith("v") and int(meta.version[1:]) >= 7
    assert meta.title == "Attention Is All You Need"
    assert "Ashish Vaswani" in meta.authors and len(meta.authors) == 8
    assert meta.abstract.startswith("The dominant sequence transduction models")
    assert meta.published is not None and meta.published.year == 2017


async def test_a_pinned_version_is_kept_and_its_pdf_downloads() -> None:
    async with ArxivClient() as client:
        meta = await client.metadata(parse_arxiv_id("arxiv.org/pdf/1412.6980v8"))
        assert meta.version == "v8"
        pdf = await client.download_pdf(meta.arxiv_id, meta.version)
    assert pdf.startswith(b"%PDF-")
    assert extract_pdf(pdf).num_pages > 5


async def test_an_unknown_id_is_not_found() -> None:
    async with ArxivClient() as client:
        with pytest.raises(ArxivNotFound):
            await client.metadata(parse_arxiv_id("1312.99999"))


async def test_ingest_arxiv_end_to_end(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "papers")
    ingestor = Ingestor(store, embedder=HashEmbedder(), cache=EmbeddingCache(tmp_path / "embeddings"))
    record = await ingestor.ingest_arxiv(f"{TRANSFORMER}v7")

    meta = record.meta
    assert (meta.paper_id, meta.arxiv_version, meta.chunker_version) == (TRANSFORMER, "v7", CHUNKER_VERSION)
    assert meta.num_pages == 15 and meta.title == "Attention Is All You Need"
    numbered = [(s.number, s.title) for s in meta.sections if s.number]
    assert ("3.2.1", "Scaled Dot-Product Attention") in numbered
    assert ("5.2", "Hardware and Schedule") in numbered
    assert [s.section_id for s in meta.sections][-2:] == ["sec_references", "sec_attention_visualizations"]

    ids = {c.chunk_id for c in record.chunks}
    assert f"{TRANSFORMER}:sec_3.2.1_scaled_dot_product_attention" in ids
    scaled = store.chunk(TRANSFORMER, f"{TRANSFORMER}:sec_3.2.1_scaled_dot_product_attention")
    assert scaled is not None and "d_k" in scaled.text and scaled.boxes
    # The footnote with the variance argument travels with the section that cites it.
    assert "variance dk" in scaled.text or "variance d_k" in scaled.text
    assert store.pdf_path(TRANSFORMER).read_bytes().startswith(b"%PDF-")
