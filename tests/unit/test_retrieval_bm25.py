"""The lexical arm: BM25Plus by default, with "no shared term" scoring exactly 0."""

import numpy as np
import pytest

from papermind.retrieval.bm25 import DEFAULT_VARIANT, K1, B, BM25Index

DOCS = [
    "Scaled dot-product attention divides the dot products by the square root of d_k.",
    "Multi-head attention projects queries, keys and values h times.",
    "We trained on 8 NVIDIA P100 GPUs for 12 hours.",
    "Label smoothing and residual dropout regularize training.",
]


def test_defaults_are_the_documented_ones():
    assert DEFAULT_VARIANT == "plus"
    assert (K1, B) == (1.5, 0.75)


@pytest.mark.parametrize("variant", ["plus", "okapi"])
def test_the_matching_document_ranks_first(variant):
    index = BM25Index(DOCS, variant=variant)
    scores = index.scores("square root of d_k")
    assert int(np.argmax(scores)) == 0
    assert int(np.argmax(index.scores("P100 GPUs"))) == 2


def test_documents_sharing_no_term_score_zero():
    index = BM25Index(DOCS)
    scores = index.scores("P100 GPUs")
    assert scores[2] > 0
    assert scores[0] == scores[1] == scores[3] == 0.0


def test_a_term_in_most_documents_still_counts_with_plus():
    # 'attention' is in 3 of 4 documents. Okapi's IDF for it is negative and
    # floored near zero; Plus keeps it positive, so the term still separates
    # documents that have it from those that do not.
    docs = [
        "attention attention",
        "attention heads",
        "attention maps",
        "dropout",
    ]
    plus = BM25Index(docs, variant="plus").scores("attention")
    assert all(plus[i] > 0 for i in range(3)) and plus[3] == 0.0
    assert plus[0] > plus[1]


def test_two_document_corpus_is_not_all_zero():
    # rank_bm25's Okapi IDF floor makes every score 0 here; Plus does not.
    index = BM25Index(["adam optimizer moments", "batch normalization statistics"])
    scores = index.scores("adam moments")
    assert scores[0] > 0 and scores[1] == 0.0


@pytest.mark.parametrize("docs", [[], [""], ["", "  "]])
def test_empty_corpora_score_nothing(docs):
    index = BM25Index(docs)
    assert index.scores("anything").tolist() == [0.0] * len(docs)


def test_query_with_no_tokens_scores_nothing():
    index = BM25Index(DOCS)
    assert index.scores("?!").tolist() == [0.0] * len(DOCS)
