"""The lexical arm: BM25 over one paper's chunks (rank_bm25).

Variant. A per-paper corpus is tiny (25-60 chunks), which is where the classic
BM25 (``BM25Okapi``) misbehaves: its IDF, log((N - n + 0.5) / (n + 0.5)), goes
negative for any term in more than half the chunks and rank_bm25 floors it to
a quarter of the average IDF. Central terms of a paper -- 'attention' in the
Transformer paper, 'lora' in LoRA -- sit in most chunks, so they contribute
almost nothing, and a two-chunk corpus scores everything zero.

rank_bm25's ``BM25Plus`` uses log((N + 1) / n), which stays positive. Its
``delta`` is added for every query term to every document, matched or not, so
it shifts all scores by the same amount and does not change the ranking: what
this class ranks by is BM25 with that positive IDF. The shift is removed in
``scores`` so that 0 still means "shares no term with the query".

Measured on the eight benchmark papers with 48 natural-language questions
(the 24 of ``scripts/ingest_benchmark.py --smoke`` plus 24 more), lexical arm
alone: Okapi hit@5 44/48, MRR@10 0.72; this variant 45/48, 0.74; a textbook
BM25+ (delta only on matched terms) 45/48, 0.72; Lucene's IDF, log(1 + (N - n
+ 0.5) / (n + 0.5)), 45/48, 0.75. The positive-IDF variants are within a
question or two of each other, which is noise at this size, so the stock
rank_bm25 class is the default rather than a hand-tuned one.

Parameters are rank_bm25's defaults, which are the standard literature values:
``k1 = 1.5`` (term-frequency saturation: the 3rd occurrence of a word adds much
less than the 1st) and ``b = 0.75`` (length normalisation: long chunks are
discounted, but not fully, since our chunks are already length-capped).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
from rank_bm25 import BM25Okapi, BM25Plus

from papermind.retrieval.text import tokenize

BM25Variant = Literal["plus", "okapi"]
DEFAULT_VARIANT: BM25Variant = "plus"
K1 = 1.5
B = 0.75
PLUS_DELTA = 1.0


class BM25Index:
    """BM25 scores for one corpus. Build and query are CPU-bound but take milliseconds."""

    def __init__(self, documents: Sequence[str], *, variant: BM25Variant = DEFAULT_VARIANT) -> None:
        self.size = len(documents)
        self.variant = variant
        corpus = [tokenize(doc) for doc in documents]
        # rank_bm25 divides by the corpus size and the average length; an empty
        # corpus (or one with no tokens at all) has nothing to rank anyway.
        self._bm25: BM25Okapi | BM25Plus | None = None
        if corpus and any(corpus):
            if variant == "plus":
                self._bm25 = BM25Plus(corpus, k1=K1, b=B, delta=PLUS_DELTA)
            else:
                self._bm25 = BM25Okapi(corpus, k1=K1, b=B)

    def scores(self, query: str) -> np.ndarray:
        """One score per document; 0 for documents sharing no term with the query."""
        tokens = tokenize(query)
        if self._bm25 is None or not tokens:
            return np.zeros(self.size, dtype=np.float64)
        scores = np.asarray(self._bm25.get_scores(tokens), dtype=np.float64)
        if self.variant == "plus":
            # BM25Plus gives every document delta * idf for every query term,
            # matched or not, so "no match" is not 0. Normalise that floor
            # away so callers can treat score <= 0 as "shares no term".
            matched = self._matches(tokens)
            scores = np.where(matched, scores, 0.0)
        return scores

    def _matches(self, tokens: list[str]) -> np.ndarray:
        assert self._bm25 is not None
        unique = set(tokens)
        return np.array(
            [any(token in freqs for token in unique) for freqs in self._bm25.doc_freqs],
            dtype=bool,
        )
