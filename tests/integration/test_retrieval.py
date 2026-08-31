"""Retrieval end to end over the real ingested paper.

Marked `integration`: needs the embedding model weights (downloaded on first
run) and an ingested paper on disk. The fusion and BM25 logic is covered
offline in tests/unit.

    pytest -m integration
"""

from pathlib import Path

import pytest
from papermind.ingest import ingest_paper
from papermind.ingest.arxiv import make_client
from papermind.ingest.store import save_paper
from papermind.retrieval import HybridRetriever, IndexRegistry, RetrievalMode

pytestmark = pytest.mark.integration

PAPER_ID = "1706.03762"


@pytest.fixture(scope="module")
async def registry(tmp_path_factory: pytest.TempPathFactory):
    """An isolated papers dir with one ingested paper, indexed."""
    papers_dir = tmp_path_factory.mktemp("papers")
    async with make_client() as client:
        result = await ingest_paper(
            f"{PAPER_ID}v7",
            client=client,
            pdf_dir=tmp_path_factory.mktemp("pdfs"),
        )
    save_paper(papers_dir, result)

    registry = IndexRegistry(papers_dir)
    await registry.warm()
    return registry


@pytest.fixture(scope="module")
def retriever(registry) -> HybridRetriever:
    return HybridRetriever(registry)


async def test_warm_builds_every_ingested_paper(registry) -> None:
    assert registry.loaded_paper_ids() == [PAPER_ID]


async def test_dense_index_windows_oversized_chunks(registry) -> None:
    """More windows than chunks: the encoder limit is being worked around.

    If these were equal, most chunks would be silently truncated at 256 tokens.
    """
    index = await registry.get(PAPER_ID)
    assert index.dense.n_windows > len(index)


async def test_all_three_paths_share_one_signature(retriever) -> None:
    """The property the ablation depends on."""
    for path in retriever.paths().values():
        hits = await path("what is multi-head attention?", PAPER_ID, 3)
        assert len(hits) == 3
        assert all(h.paper_id == PAPER_ID for h in hits)


@pytest.mark.parametrize("mode", list(RetrievalMode))
async def test_k_is_respected(retriever, mode: RetrievalMode) -> None:
    for k in (1, 3, 5):
        assert len(await retriever.search("attention", PAPER_ID, k=k, mode=mode)) == k


async def test_search_defaults_to_hybrid(retriever) -> None:
    query = "how are positions encoded?"
    assert [c.id for c in await retriever.search(query, PAPER_ID, k=5)] == [
        c.id for c in await retriever.hybrid(query, PAPER_ID, k=5)
    ]


async def test_lexical_query_finds_its_section(retriever) -> None:
    """An exact-token query is what BM25 is in the mix for."""
    hits = await retriever.bm25_only("eight NVIDIA P100 GPUs", PAPER_ID, k=3)
    assert f"{PAPER_ID}:sec_5.2_hardware_and_schedule" in {h.id for h in hits}


async def test_paraphrased_query_finds_its_section(retriever) -> None:
    """Zero lexical overlap with the target section -- BM25's blind spot.

    'how much computing power' shares no token with 'Hardware and Schedule'.
    The dense arm puts it first; BM25 does not reach it in three.
    """
    query = "how much computing power was needed to train it?"
    target = f"{PAPER_ID}:sec_5.2_hardware_and_schedule"

    dense = [c.id for c in await retriever.dense_only(query, PAPER_ID, k=3)]
    bm25 = [c.id for c in await retriever.bm25_only(query, PAPER_ID, k=3)]

    assert dense[0] == target
    assert target not in bm25


async def test_hybrid_recovers_a_section_bm25_ranks_poorly(retriever) -> None:
    """The case that justifies fusion at all.

    BM25 puts sec_5.3_optimizer first here -- the optimizer section is full of
    'rate', 'square' and 'root' from the learning-rate schedule. The section
    that actually answers the question wins on the dense arm, and fusion
    surfaces it.

    Note the phrasing matters: adding 'dot products' gives BM25 enough lexical
    signal to get it right on its own, and the effect disappears.
    """
    query = "why divide by the square root of d_k?"
    target = f"{PAPER_ID}:sec_3.2.1_scaled_dot_product_attention"

    hybrid = [c.id for c in await retriever.hybrid(query, PAPER_ID, k=3)]
    bm25 = [c.id for c in await retriever.bm25_only(query, PAPER_ID, k=3)]

    assert hybrid[0] == target
    assert bm25[0] != target


async def test_scored_results_report_each_arms_rank(retriever) -> None:
    """Provenance: which arm surfaced this, and where."""
    scored = await retriever.search_scored(
        "how does the model represent token order?", PAPER_ID, k=5
    )
    assert len(scored) == 5
    assert [s.rank for s in scored] == [1, 2, 3, 4, 5]
    assert all(s.score > 0 for s in scored)
    # At least one result must be one the lexical arm ranked poorly or missed --
    # otherwise the dense arm contributed nothing and fusion is doing no work.
    assert any(s.bm25_rank is None or s.bm25_rank > s.rank for s in scored)


async def test_results_are_deterministic(retriever) -> None:
    """Reruns must not reorder, or ablation numbers are noise."""
    query = "what optimizer was used?"
    for mode in RetrievalMode:
        first = [c.id for c in await retriever.search(query, PAPER_ID, k=5, mode=mode)]
        second = [c.id for c in await retriever.search(query, PAPER_ID, k=5, mode=mode)]
        assert first == second


async def test_retrieval_is_scoped_to_one_paper(retriever) -> None:
    hits = await retriever.search("attention", PAPER_ID, k=5)
    assert {h.paper_id for h in hits} == {PAPER_ID}


async def test_uningested_paper_raises_a_useful_error(registry) -> None:
    with pytest.raises(KeyError, match="has not been ingested"):
        await registry.get("9999.99999")


async def test_concurrent_first_requests_build_the_index_once(tmp_path: Path, registry) -> None:
    """Two coroutines racing on a cold paper must not both build it."""
    import asyncio

    fresh = IndexRegistry(registry.papers_dir)
    a, b = await asyncio.gather(fresh.get(PAPER_ID), fresh.get(PAPER_ID))
    assert a is b
