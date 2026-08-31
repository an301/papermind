"""The retriever: BM25, dense, and their RRF fusion.

Three named paths -- `bm25_only`, `dense_only`, `hybrid` -- share one signature:

    (query: str, paper_id: str, k: int = 5) -> list[Chunk]

Identical signatures on purpose. An ablation should be able to iterate over
`retriever.paths()` and swap arms with no other change, so that the only
difference between runs is the retrieval strategy itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import StrEnum

from papermind.ingest.models import Chunk
from papermind.retrieval.fusion import RRF_K, rank_positions, reciprocal_rank_fusion
from papermind.retrieval.index import IndexRegistry, PaperIndex
from papermind.retrieval.models import ScoredChunk

# Candidate depth per arm before fusion, as a multiple of the requested k.
#
# Fusing only the top k from each arm throws away the recall that makes fusion
# worth doing: a chunk ranked 7th by BM25 and 6th by dense should beat one
# ranked 1st by BM25 and nowhere by dense, and at k=5 neither arm would have
# reported it. Going deeper costs nothing here -- both arms already scored the
# whole paper.
_FETCH_MULTIPLIER = 4
_MIN_FETCH = 20


def _fetch_depth(k: int) -> int:
    return max(k * _FETCH_MULTIPLIER, _MIN_FETCH)


class RetrievalMode(StrEnum):
    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"


class HybridRetriever:
    """Retrieval over ingested papers, scoped to one paper per query."""

    def __init__(self, registry: IndexRegistry, *, rrf_k: int = RRF_K) -> None:
        self._registry = registry
        self._rrf_k = rrf_k

    # ---- the three ablation paths -------------------------------------------

    async def bm25_only(self, query: str, paper_id: str, k: int = 5) -> list[Chunk]:
        """Lexical retrieval alone."""
        index = await self._registry.get(paper_id)
        ranked = await self._rank_bm25(index, query, k)
        return index.resolve(ranked[:k])

    async def dense_only(self, query: str, paper_id: str, k: int = 5) -> list[Chunk]:
        """Embedding retrieval alone."""
        index = await self._registry.get(paper_id)
        ranked = await self._rank_dense(index, query, k)
        return index.resolve(ranked[:k])

    async def hybrid(self, query: str, paper_id: str, k: int = 5) -> list[Chunk]:
        """Both arms, merged with Reciprocal Rank Fusion."""
        index = await self._registry.get(paper_id)
        fused, _, _ = await self._fuse(index, query, k)
        return index.resolve([chunk_id for chunk_id, _ in fused[:k]])

    # ---- dispatch ------------------------------------------------------------

    async def search(
        self,
        query: str,
        paper_id: str,
        k: int = 5,
        mode: RetrievalMode = RetrievalMode.HYBRID,
    ) -> list[Chunk]:
        """The interface the agent's retriever tool calls. Hybrid by default."""
        return await self.paths()[mode](query, paper_id, k)

    def paths(self) -> dict[RetrievalMode, Callable[[str, str, int], Awaitable[list[Chunk]]]]:
        """The three arms, keyed by mode -- the ablation's entry point."""
        return {
            RetrievalMode.BM25: self.bm25_only,
            RetrievalMode.DENSE: self.dense_only,
            RetrievalMode.HYBRID: self.hybrid,
        }

    async def search_scored(
        self,
        query: str,
        paper_id: str,
        k: int = 5,
        mode: RetrievalMode = RetrievalMode.HYBRID,
    ) -> list[ScoredChunk]:
        """Same results, with scores and each arm's rank attached.

        For analysis, not for the agent -- `search` is what the tool calls.
        """
        index = await self._registry.get(paper_id)

        if mode is RetrievalMode.HYBRID:
            fused, bm25_ranks, dense_ranks = await self._fuse(index, query, k)
        elif mode is RetrievalMode.BM25:
            ranked = await self._rank_bm25(index, query, k)
            bm25_ranks, dense_ranks = rank_positions(ranked), {}
            fused = [(cid, 1.0 / rank) for cid, rank in bm25_ranks.items()]
        else:
            ranked = await self._rank_dense(index, query, k)
            bm25_ranks, dense_ranks = {}, rank_positions(ranked)
            fused = [(cid, 1.0 / rank) for cid, rank in dense_ranks.items()]

        results: list[ScoredChunk] = []
        for rank, (chunk_id, score) in enumerate(fused[:k], start=1):
            chunk = index.by_id.get(chunk_id)
            if chunk is None:
                continue
            results.append(
                ScoredChunk(
                    chunk=chunk,
                    score=score,
                    rank=rank,
                    bm25_rank=bm25_ranks.get(chunk_id),
                    dense_rank=dense_ranks.get(chunk_id),
                )
            )
        return results

    # ---- internals -----------------------------------------------------------

    async def _rank_bm25(self, index: PaperIndex, query: str, k: int) -> list[str]:
        return await asyncio.to_thread(index.bm25.rank, query, _fetch_depth(k))

    async def _rank_dense(self, index: PaperIndex, query: str, k: int) -> list[str]:
        # Encoding the query is the slowest step in a search; torch releases the
        # GIL during the forward pass, so a thread genuinely gets off the loop.
        return await asyncio.to_thread(index.dense.rank, query, _fetch_depth(k))

    async def _fuse(
        self, index: PaperIndex, query: str, k: int
    ) -> tuple[list[tuple[str, float]], dict[str, int], dict[str, int]]:
        # The arms are independent, so run them concurrently: hybrid then costs
        # about what dense alone costs, rather than the sum.
        bm25_ranked, dense_ranked = await asyncio.gather(
            self._rank_bm25(index, query, k),
            self._rank_dense(index, query, k),
        )
        fused = reciprocal_rank_fusion(
            {"bm25": bm25_ranked, "dense": dense_ranked},
            rrf_k=self._rrf_k,
        )
        return fused, rank_positions(bm25_ranked), rank_positions(dense_ranked)
