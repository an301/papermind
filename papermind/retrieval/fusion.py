"""Reciprocal Rank Fusion: merge rankings by rank, never by score.

BM25 scores are unbounded and corpus-dependent; cosine similarities live in a
narrow band near 0.5-0.9. Any weighted sum of the two needs per-paper tuning
and still lets one arm dominate. RRF uses only positions:

    score(d) = sum over rankings r containing d of 1 / (RRF_K + rank_r(d))

with 1-based ranks. ``RRF_K = 60`` is the constant from Cormack, Clarke and
Büttcher (SIGIR 2009); it flattens the curve so that rank 1 vs rank 2 in one
arm matters less than appearing in both arms at all. The name is RRF_K and
never ``k``, which everywhere else in this codebase means "how many results".
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from typing import TypeVar

RRF_K = 60

T = TypeVar("T", bound=Hashable)


def reciprocal_rank_fusion(rankings: Sequence[Sequence[T]], *, rrf_k: int = RRF_K) -> list[tuple[T, float]]:
    """Fuse ranked lists (best first) into one, best first, with RRF scores.

    Ties (equal fused score) keep the order in which items were first seen,
    walking the rankings in the order given, so the output is deterministic.
    """
    if rrf_k < 0:
        raise ValueError("RRF_K must be non-negative")
    scores: dict[T, float] = {}
    first_seen: dict[T, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (rrf_k + rank)
            first_seen.setdefault(item, len(first_seen))
    return sorted(scores.items(), key=lambda pair: (-pair[1], first_seen[pair[0]]))
