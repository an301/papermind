"""HybridRetriever: the three ablation paths, fusion depth, section filters and result fields."""

from pathlib import Path

import pytest

from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.store import PaperStore
from papermind.retrieval import hybrid as hybrid_module
from papermind.retrieval.fusion import RRF_K
from papermind.retrieval.hybrid import HybridRetriever, arm_depth
from papermind.retrieval.index import IndexRegistry, PaperNotFound
from tests.unit.conftest import TRANSFORMER_LIKE, HashEmbedder, make_record

PAPER = "1706.03762"


@pytest.fixture
def retriever(tmp_path: Path) -> HybridRetriever:
    store = PaperStore(tmp_path / "papers")
    store.save(make_record(PAPER, TRANSFORMER_LIKE, chunker_version=CHUNKER_VERSION), b"%PDF-1.4\n")
    # Wide enough that hash-embedding noise cannot outrank real word overlap.
    return HybridRetriever(IndexRegistry(store, HashEmbedder(dim=1024)))


def sections(results) -> list[str | None]:
    return [r.section_number for r in results]


def test_arm_depth_is_max_of_4k_and_20():
    assert arm_depth(1) == 20
    assert arm_depth(5) == 20
    assert arm_depth(10) == 40


def test_paths_are_the_three_ablation_arms(retriever: HybridRetriever):
    assert set(retriever.paths()) == {"bm25", "dense", "hybrid"}


def test_rrf_k_comes_from_settings_by_default(retriever: HybridRetriever):
    assert retriever.rrf_k == RRF_K


async def test_bm25_finds_notation(retriever: HybridRetriever):
    results = await retriever.bm25_only("square root of d_k", PAPER, k=3)
    assert sections(results)[0] == "3.2.1"
    assert all(r.dense_rank is None and r.bm25_rank == r.rank for r in results)
    assert [r.rank for r in results] == list(range(1, len(results) + 1))


async def test_bm25_returns_only_documents_sharing_a_term(retriever: HybridRetriever):
    results = await retriever.bm25_only("P100 GPUs", PAPER, k=5)
    assert sections(results) == ["5.2"]


async def test_dense_ranks_every_chunk_with_text(retriever: HybridRetriever):
    results = await retriever.dense_only("positional encodings inject token order", PAPER, k=20)
    assert len(results) == len(TRANSFORMER_LIKE)
    assert sections(results)[0] == "3.5"
    assert all(r.bm25_rank is None and r.dense_rank == r.rank for r in results)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


async def test_hybrid_fuses_both_arms(retriever: HybridRetriever):
    results = await retriever.hybrid("divide by the square root of d_k", PAPER, k=5)
    assert len(results) == 5
    top = results[0]
    assert top.section_number == "3.2.1"
    assert top.bm25_rank == 1 and top.dense_rank is not None
    assert top.score == pytest.approx(1 / (RRF_K + top.bm25_rank) + 1 / (RRF_K + top.dense_rank), abs=1e-6)
    assert [r.rank for r in results] == [1, 2, 3, 4, 5]
    assert all(r.chunk_id.startswith(f"{PAPER}:sec_") for r in results)


async def test_search_dispatches_on_mode(retriever: HybridRetriever):
    for mode in ("bm25", "dense", "hybrid"):
        results = await retriever.search("label smoothing dropout", PAPER, k=2, mode=mode)
        assert results and results[0].section_number == "5.4", mode
    with pytest.raises(ValueError):
        await retriever.search("x y", PAPER, mode="tfidf")  # type: ignore[arg-type]


async def test_section_filter_restricts_every_arm(retriever: HybridRetriever):
    for mode in ("bm25", "dense", "hybrid"):
        results = await retriever.search("attention", PAPER, k=5, mode=mode, section_filter="3.2")
        assert results, mode
        assert set(sections(results)) <= {"3.2.1", "3.2.2"}, mode
    assert await retriever.search("attention", PAPER, section_filter="9") == []


async def test_results_carry_chunk_fields(retriever: HybridRetriever):
    (result,) = await retriever.search("P100 GPUs schedule", PAPER, k=1)
    assert result.chunk_id == f"{PAPER}:sec_5.2_hardware_and_schedule"
    assert result.section_id == "sec_5.2"
    assert result.section_title == "Hardware and Schedule"
    assert "P100" in result.text
    assert result.page_start == result.page_end == 2


async def test_each_arm_contributes_max_4k_20_before_fusion(retriever: HybridRetriever, monkeypatch):
    depths: list[int] = []
    original = hybrid_module._top

    def spy(scores, mask, depth):
        depths.append(depth)
        return original(scores, mask, depth)

    monkeypatch.setattr(hybrid_module, "_top", spy)
    await retriever.hybrid("attention", PAPER, k=3)
    await retriever.hybrid("attention", PAPER, k=8)
    assert depths == [20, 20, 32, 32]


async def test_invalid_k_and_unknown_paper(retriever: HybridRetriever):
    with pytest.raises(ValueError):
        await retriever.hybrid("attention", PAPER, k=0)
    with pytest.raises(PaperNotFound):
        await retriever.search("attention", "9999.99999")
