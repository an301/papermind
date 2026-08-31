"""End-to-end ingestion against the real arXiv. Network required.

Marked `integration` so the default `pytest` run stays offline and fast:

    pytest -m integration
"""

from pathlib import Path

import pytest
from papermind.ingest import ingest_paper
from papermind.ingest.arxiv import make_client
from papermind.ingest.store import load_paper, save_paper, update_manifest

pytestmark = pytest.mark.integration

TRANSFORMER = "1706.03762v7"  # version-pinned: the assertions below are about its text


@pytest.fixture(scope="module")
async def result(tmp_path_factory: pytest.TempPathFactory):
    async with make_client() as client:
        return await ingest_paper(
            TRANSFORMER,
            client=client,
            pdf_dir=tmp_path_factory.mktemp("pdfs"),
        )


async def test_metadata_is_resolved_and_pinned(result) -> None:
    assert result.meta.paper_id == "1706.03762"
    assert result.meta.versioned_id == TRANSFORMER
    assert result.meta.title == "Attention Is All You Need"
    assert result.meta.n_pages == 15
    assert len(result.meta.authors) == 8
    assert len(result.meta.pdf_sha256) == 64


async def test_the_documented_example_id_exists(result) -> None:
    """The ID format from the spec, on the paper it was specified against."""
    ids = {c.id for c in result.chunks}
    assert "1706.03762:sec_3.2_attention" in ids


async def test_sections_map_to_the_papers_real_structure(result) -> None:
    ids = {c.id for c in result.chunks}
    for expected in [
        "1706.03762:sec_abstract",
        "1706.03762:sec_1_introduction",
        "1706.03762:sec_3_model_architecture",
        "1706.03762:sec_3.2.1_scaled_dot_product_attention",
        "1706.03762:sec_3.2.2_multi_head_attention",
        "1706.03762:sec_5.3_optimizer",
        "1706.03762:sec_7_conclusion",
    ]:
        assert expected in ids, expected


async def test_no_chunk_exceeds_the_budget(result) -> None:
    assert all(c.token_count <= result.max_chunk_tokens for c in result.chunks)


async def test_text_quality(result) -> None:
    import re

    joined = " ".join(c.text for c in result.chunks)
    # Words glued together by dropped whitespace spans.
    assert not re.search(r"[a-z][.,;:][A-Z][a-z]", joined)
    # Hyphenated line breaks left unrepaired.
    assert not re.search(r"[a-z]- [a-z]", joined)
    # Ligatures that would break exact-match retrieval.
    assert not re.search(r"[ﬁﬂﬀﬃﬄ]", joined)
    # The arXiv margin stamp is not part of any section.
    assert "arXiv:1706.03762" not in joined


async def test_prose_is_not_interrupted_by_a_figure_caption(result) -> None:
    chunk = next(c for c in result.chunks if c.id == "1706.03762:sec_3.2_attention")
    assert "weighted sum" in chunk.text
    # The sentence survives; the caption is appended after it.
    assert chunk.text.index("Figure 2") > chunk.text.index("of the values")


async def test_only_long_sections_split(result) -> None:
    split = {c.section_title for c in result.chunks if c.n_parts > 1}
    assert split == {"References"}


async def test_round_trip_through_disk(result, tmp_path: Path) -> None:
    save_paper(tmp_path, result)
    reloaded = load_paper(tmp_path, "1706.03762")
    assert reloaded is not None
    assert [c.id for c in reloaded.chunks] == [c.id for c in result.chunks]

    update_manifest(tmp_path)
    assert (tmp_path / "manifest.json").exists()


async def test_ingestion_is_reproducible(result, tmp_path: Path) -> None:
    """Same input, same IDs -- re-running must not relabel the benchmark."""
    async with make_client() as client:
        again = await ingest_paper(TRANSFORMER, client=client, pdf_dir=tmp_path)
    assert [c.id for c in again.chunks] == [c.id for c in result.chunks]
    assert [c.text for c in again.chunks] == [c.text for c in result.chunks]
    assert again.meta.pdf_sha256 == result.meta.pdf_sha256
