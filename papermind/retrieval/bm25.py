"""The lexical arm: BM25 over one paper's chunks.

Scoped per paper, which changes what BM25 means here. IDF is computed over the
~30 chunks of the paper being read, not over a corpus -- so a term is "rare"
relative to *this paper*. That is the right frame for the task: a reader asking
about 'positional encoding' while reading the Transformer wants the section that
concentrates on it, not the section that is unusual across all of ML.
"""

from __future__ import annotations

from rank_bm25 import BM25Okapi

from papermind.ingest.models import Chunk
from papermind.retrieval.text import indexable_text, tokenize


class Bm25Index:
    """An in-memory BM25 index over a fixed list of chunks."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        # BM25Okapi averages document length at construction and so divides by
        # the document count -- an empty corpus raises ZeroDivisionError. Guard
        # rather than let one text-less paper break index building.
        if not chunks:
            self._bm25 = None
            return

        corpus = [tokenize(indexable_text(chunk)) for chunk in chunks]
        # BM25Okapi floors negative IDF (which the Okapi formula produces for
        # terms in more than half the documents) at a fraction of the average.
        # With ~30 documents per paper that case is common, so the flooring
        # matters more here than it would over a large corpus.
        self._bm25 = BM25Okapi(corpus)

    def __len__(self) -> int:
        return len(self._chunks)

    def rank(self, query: str, limit: int) -> list[str]:
        """Chunk ids, best first. Ties break on id for determinism."""
        tokens = tokenize(query)
        if not tokens or self._bm25 is None:
            return []

        scores = self._bm25.get_scores(tokens)
        ordered = sorted(
            zip(self._chunks, scores, strict=True),
            key=lambda pair: (-pair[1], pair[0].id),
        )
        # Drop non-matches. A zero score means no query term occurs in the
        # chunk; carrying it into the fusion would give a arbitrary rank to a
        # document the lexical arm has no opinion about.
        return [chunk.id for chunk, score in ordered[:limit] if score > 0.0]
