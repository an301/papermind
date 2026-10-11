"""Hybrid retrieval: BM25 and dense rankings fused with Reciprocal Rank Fusion.

The two arms fail differently. BM25 nails exact notation and names ('d_k',
'WMT 2014', 'Adam') and misses paraphrase ('how much compute did training
take' never says 'P100'); the dense arm gets paraphrase and blurs notation.
Fusing ranks keeps what each arm is sure about.

Each arm contributes its top ``max(4k, 20)`` before fusion. Fusing only the
top k of each would drop a chunk that both arms rank 7th -- exactly the
"moderately relevant to both" chunk RRF exists to promote -- while fusing
everything lets the long tail of near-zero matches add noise.

``bm25_only`` / ``dense_only`` / ``hybrid`` are the three paths of the
retrieval ablation (``paths()``); ``search`` dispatches on ``mode``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import numpy as np

from papermind.config import get_settings
from papermind.retrieval.fusion import reciprocal_rank_fusion
from papermind.retrieval.index import IndexRegistry, PaperIndex
from papermind.schemas.paper import Chunk
from papermind.schemas.tools import RetrievalMode, RetrievedChunk

SearchPath = Callable[..., Awaitable[list[RetrievedChunk]]]


def arm_depth(k: int) -> int:
    return max(4 * k, 20)


def _top(scores: np.ndarray, mask: np.ndarray, depth: int) -> list[tuple[int, float]]:
    """(position, score) of the best ``depth`` allowed chunks, best first; ties by document order."""
    candidates = np.flatnonzero(mask)
    if not len(candidates):
        return []
    order = sorted(candidates.tolist(), key=lambda pos: (-float(scores[pos]), pos))
    return [(pos, float(scores[pos])) for pos in order[:depth]]


def _result(
    chunk: Chunk, rank: int, score: float, bm25_rank: int | None, dense_rank: int | None
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk.chunk_id,
        section_id=chunk.section_id,
        section_number=chunk.section_number,
        section_title=chunk.section_title,
        text=chunk.text,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        rank=rank,
        score=round(score, 6),
        bm25_rank=bm25_rank,
        dense_rank=dense_rank,
    )


class HybridRetriever:
    def __init__(self, registry: IndexRegistry, *, rrf_k: int | None = None) -> None:
        self.registry = registry
        self.rrf_k = get_settings().rrf_k if rrf_k is None else rrf_k

    async def search(
        self,
        query: str,
        paper_id: str,
        k: int = 5,
        mode: RetrievalMode = "hybrid",
        section_filter: str | None = None,
    ) -> list[RetrievedChunk]:
        path = self.paths().get(mode)
        if path is None:
            raise ValueError(f"unknown retrieval mode {mode!r}")
        return await path(query, paper_id, k, section_filter=section_filter)

    async def bm25_only(
        self, query: str, paper_id: str, k: int = 5, *, section_filter: str | None = None
    ) -> list[RetrievedChunk]:
        index = await self.registry.get(paper_id)
        ranked = await self._bm25(index, query, index.allowed(section_filter), _check_k(k))
        return [_result(index.chunks[pos], r, s, r, None) for r, (pos, s) in enumerate(ranked, start=1)]

    async def dense_only(
        self, query: str, paper_id: str, k: int = 5, *, section_filter: str | None = None
    ) -> list[RetrievedChunk]:
        index = await self.registry.get(paper_id)
        ranked = await self._dense(index, query, index.allowed(section_filter), _check_k(k))
        return [_result(index.chunks[pos], r, s, None, r) for r, (pos, s) in enumerate(ranked, start=1)]

    async def hybrid(
        self, query: str, paper_id: str, k: int = 5, *, section_filter: str | None = None
    ) -> list[RetrievedChunk]:
        index = await self.registry.get(paper_id)
        mask = index.allowed(section_filter)
        depth = arm_depth(_check_k(k))
        lexical, semantic = await asyncio.gather(
            self._bm25(index, query, mask, depth), self._dense(index, query, mask, depth)
        )
        bm25_rank = {pos: r for r, (pos, _) in enumerate(lexical, start=1)}
        dense_rank = {pos: r for r, (pos, _) in enumerate(semantic, start=1)}
        fused = reciprocal_rank_fusion(
            [[pos for pos, _ in lexical], [pos for pos, _ in semantic]], rrf_k=self.rrf_k
        )
        return [
            _result(index.chunks[pos], r, score, bm25_rank.get(pos), dense_rank.get(pos))
            for r, (pos, score) in enumerate(fused[:k], start=1)
        ]

    def paths(self) -> dict[str, SearchPath]:
        """The ablation's three arms, by the name the eval harness reports them under."""
        return {"bm25": self.bm25_only, "dense": self.dense_only, "hybrid": self.hybrid}

    async def _bm25(
        self, index: PaperIndex, query: str, mask: np.ndarray, depth: int
    ) -> list[tuple[int, float]]:
        scores = await asyncio.to_thread(index.bm25.scores, query)
        # A chunk sharing no term with the query is not a BM25 result at all;
        # ranking it would hand RRF arbitrary positions.
        return _top(scores, mask & (scores > 0), depth)

    async def _dense(
        self, index: PaperIndex, query: str, mask: np.ndarray, depth: int
    ) -> list[tuple[int, float]]:
        vector = await asyncio.to_thread(self.registry.embedder.embed_query, query)
        scores = await asyncio.to_thread(index.dense.scores, vector)
        return _top(scores, mask & np.isfinite(scores), depth)


def _check_k(k: int) -> int:
    if k < 1:
        raise ValueError("k must be at least 1")
    return k
