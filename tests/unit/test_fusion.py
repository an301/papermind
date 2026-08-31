"""Reciprocal Rank Fusion. Pure rank math -- no model, no index, no network."""

from papermind.retrieval.fusion import RRF_K, rank_positions, reciprocal_rank_fusion


def test_rrf_k_is_sixty() -> None:
    assert RRF_K == 60


def test_single_ranking_is_preserved() -> None:
    fused = reciprocal_rank_fusion({"bm25": ["a", "b", "c"]})
    assert [doc for doc, _ in fused] == ["a", "b", "c"]


def test_symmetric_rankings_score_equally() -> None:
    """['a','b'] against ['b','a'] is a genuine tie -- neither arm wins."""
    fused = dict(reciprocal_rank_fusion({"bm25": ["a", "b"], "dense": ["b", "a"]}))
    assert fused["a"] == fused["b"]


def test_score_matches_the_formula() -> None:
    fused = dict(reciprocal_rank_fusion({"bm25": ["a", "b"], "dense": ["b", "a"]}))
    # 'a': rank 1 in bm25, rank 2 in dense.
    assert fused["a"] == 1 / (60 + 1) + 1 / (60 + 2)
    assert fused["b"] == 1 / (60 + 2) + 1 / (60 + 1)


def test_agreement_beats_a_single_strong_result() -> None:
    """The property that makes fusion worth doing.

    'b' is second in both arms; 'a' is first in one and absent from the other.
    Consistent mid-rank agreement should win.
    """
    fused = reciprocal_rank_fusion({"bm25": ["a", "b"], "dense": ["c", "b"]})
    assert fused[0][0] == "b"


def test_documents_missing_from_an_arm_are_not_penalised() -> None:
    """RRF has no penalty term -- an absent doc just gets no contribution."""
    fused = dict(reciprocal_rank_fusion({"bm25": ["a"], "dense": []}))
    assert fused["a"] == 1 / 61


def test_arms_of_different_lengths_fuse() -> None:
    fused = reciprocal_rank_fusion({"bm25": ["a", "b", "c", "d"], "dense": ["d"]})
    assert fused[0][0] == "d"


def test_ties_break_deterministically() -> None:
    """An ablation that reorders tied results between runs measures nothing."""
    rankings = {"bm25": ["b", "a"], "dense": ["a", "b"]}
    first = reciprocal_rank_fusion(rankings)
    assert first == reciprocal_rank_fusion(rankings)
    assert [doc for doc, _ in first] == ["a", "b"]


def test_rrf_k_changes_how_much_top_ranks_dominate() -> None:
    """Small rrf_k sharpens the top of the list; large rrf_k flattens it.

    Both arms must agree for this to be observable: with ['a','b'] against
    ['b','a'] the two documents score identically at every rrf_k, because the
    ranks are symmetric.
    """
    rankings = {"bm25": ["a", "b"], "dense": ["a", "b"]}
    sharp = dict(reciprocal_rank_fusion(rankings, rrf_k=1))
    flat = dict(reciprocal_rank_fusion(rankings, rrf_k=1000))
    assert sharp["a"] - sharp["b"] > flat["a"] - flat["b"]


def test_empty_input() -> None:
    assert reciprocal_rank_fusion({}) == []
    assert reciprocal_rank_fusion({"bm25": []}) == []


def test_rank_positions_are_one_indexed() -> None:
    assert rank_positions(["a", "b", "c"]) == {"a": 1, "b": 2, "c": 3}
