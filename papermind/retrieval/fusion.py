"""Reciprocal Rank Fusion.

    score(d) = sum over rankings r of  1 / (RRF_K + rank_r(d))

Pure functions over rank lists -- no models, no indexes -- so the fusion logic
is unit-testable without loading anything.

RRF fuses *ranks*, never scores. That is the property that makes it the right
merge for this retriever: BM25 returns unbounded relevance scores and the dense
arm returns cosine similarities in [-1, 1]. Any score-level combination needs a
normalisation step whose parameters would then need tuning per paper. Ranks are
already comparable.
"""

from __future__ import annotations

from collections import defaultdict

# The RRF smoothing constant from Cormack et al. (2009).
#
# NOTE: this is NOT the `k` of "top-k results". They are different numbers and
# conflating them is an easy, silent bug -- so the constant is spelled RRF_K
# everywhere and the result count stays `k`.
#
# Its job is to damp the influence of the very top ranks: at RRF_K=60 the gap
# between rank 1 and rank 2 (1/61 vs 1/62) is small, so one arm being confidently
# wrong cannot dominate the fusion.
RRF_K = 60


def reciprocal_rank_fusion(
    rankings: dict[str, list[str]],
    *,
    rrf_k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Fuse named rankings of ids into one ranking.

    `rankings` maps an arm name to its ordered list of ids, best first. Ids
    absent from an arm simply receive no contribution from it -- there is no
    penalty term, which is what lets RRF handle arms that return different
    numbers of candidates.

    Ties break on id so the output is deterministic; an ablation that reorders
    tied results between runs is not measuring anything.
    """
    scores: dict[str, float] = defaultdict(float)
    for ranked_ids in rankings.values():
        for rank, doc_id in enumerate(ranked_ids, start=1):
            scores[doc_id] += 1.0 / (rrf_k + rank)

    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def rank_positions(ranked_ids: list[str]) -> dict[str, int]:
    """id -> 1-indexed rank, for reporting which arm surfaced what."""
    return {doc_id: rank for rank, doc_id in enumerate(ranked_ids, start=1)}
