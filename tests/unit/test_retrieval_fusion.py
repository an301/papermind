"""Reciprocal Rank Fusion: ranks only, RRF_K = 60, deterministic ties."""

import pytest

from papermind.retrieval import fusion
from papermind.retrieval.fusion import RRF_K, reciprocal_rank_fusion


def test_constant_is_named_rrf_k_and_is_60():
    assert RRF_K == 60
    assert not hasattr(fusion, "K")


def test_scores_are_sums_of_reciprocal_ranks():
    fused = dict(reciprocal_rank_fusion([["a", "b", "c"], ["b", "a"]]))
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert fused["c"] == pytest.approx(1 / 63)


def test_appearing_in_both_lists_beats_topping_one():
    # 'x' is first in one arm only; 'y' is third in both.
    fused = reciprocal_rank_fusion([["x", "p", "y"], ["q", "r", "y"]])
    assert fused[0][0] == "y"


def test_ties_keep_first_seen_order():
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]])
    assert [item for item, _ in fused] == ["a", "b"]
    fused = reciprocal_rank_fusion([["b", "a"], ["a", "b"]])
    assert [item for item, _ in fused] == ["b", "a"]


def test_any_hashable_items_and_a_short_second_arm():
    assert [item for item, _ in reciprocal_rank_fusion([[3, 1, 2], [1]])] == [1, 3, 2]


def test_empty_and_single_rankings():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []
    assert [item for item, _ in reciprocal_rank_fusion([["a", "b", "c"]])] == ["a", "b", "c"]


def test_rrf_k_can_be_overridden_but_not_negative():
    fused = dict(reciprocal_rank_fusion([["a"]], rrf_k=0))
    assert fused["a"] == 1.0
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([["a"]], rrf_k=-1)
