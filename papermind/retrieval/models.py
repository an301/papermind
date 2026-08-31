"""Retrieval result models."""

from __future__ import annotations

from pydantic import BaseModel

from papermind.ingest.models import Chunk


class ScoredChunk(BaseModel):
    """A result with its provenance, for diagnostics and ablation analysis.

    `bm25_rank` / `dense_rank` are the 1-indexed positions the chunk held in
    each arm, or None if that arm did not return it. They are what turns "hybrid
    scored better" into "hybrid scored better *because* the dense arm surfaced
    chunks BM25 missed entirely" -- which is the interesting half of an ablation.
    """

    chunk: Chunk
    score: float
    rank: int
    bm25_rank: int | None = None
    dense_rank: int | None = None
