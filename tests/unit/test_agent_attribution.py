"""Which sentence of a cited passage the answer relied on: deterministic, and empty when unsure."""

from __future__ import annotations

from papermind.agent.attribution import (
    best_sentence,
    cited_sentences,
    content_words,
    split_sentences,
    with_cited_sentences,
    words_by_label,
)
from papermind.agent.labels import label_sources
from tests.unit.fakes import make_chunk

# Verbatim from the ingested 1706.03762 §3.2.1 chunk, line breaks included.
SDPA_TEXT = (
    'We call our particular attention "Scaled Dot-Product Attention" (Figure 2). The input consists of '
    "queries and keys of dimension d_k, and values of dimension d_v. We compute the dot products of the "
    "query with all keys, divide each by √d_k, and apply a softmax function to obtain the weights on the "
    "values.\n\nAttention(Q, K, V ) = softmax(QK^T )V (1) √d_k\n\nWhile for small values of d_k the two "
    "mechanisms perform similarly, additive attention outperforms dot product attention without scaling "
    "for larger values of d_k [3]. We suspect that for large values of d_k, the dot products grow large in "
    "magnitude, pushing the softmax function into regions where it has extremely small gradients^4."
)
TRAINING_TEXT = (
    "We trained on the standard WMT 2014 English-German dataset consisting of about 4.5 million sentence "
    "pairs. Sentences were encoded using byte-pair encoding [3], which has a shared source-target "
    "vocabulary of about 37000 tokens."
)
SOURCES = label_sources(
    [
        make_chunk("sdpa", SDPA_TEXT, number="3.2.1", title="Scaled Dot-Product Attention"),
        make_chunk("training", TRAINING_TEXT, number="5.1", title="Training Data and Batching"),
        make_chunk("mha", "Multi-head attention attends to several subspaces at once.", number="3.2.2"),
    ]
)


def test_sentences_respect_abbreviations_initials_and_paragraphs() -> None:
    text = (
        "Vaswani et al. propose attention, e.g. in Fig. 2 of the paper. A. Vaswani wrote it. "
        "It works!\nStill the same paragraph? Yes.\n\n- a list item. Another sentence.\n1. Numbered."
    )
    assert split_sentences(text) == [
        "Vaswani et al. propose attention, e.g. in Fig. 2 of the paper.",
        "A. Vaswani wrote it.",
        "It works!",
        "Still the same paragraph?",
        "Yes.",
        "- a list item.",
        "Another sentence.",
        "1. Numbered.",
    ]


def test_tex_meets_the_papers_own_notation() -> None:
    assert {"sqrt", "dk"} <= content_words("divide by $\\sqrt{d_k}$")
    assert "dk" in content_words("divide each by √d_k")
    # Stems line up across inflections; stopwords and citation markers are not words.
    assert content_words("The scaled scores [S1]") == content_words("scaling score")


def test_a_marker_after_the_full_stop_belongs_to_the_sentence_before() -> None:
    words = words_by_label("Softmax makes weights. [S1] Training used WMT [S2, S1].")
    assert {"softmax", "weight"} <= words["S1"] and {"train", "wmt"} <= words["S1"]
    assert "softmax" not in words["S2"]


def test_the_sentence_the_answer_used_is_picked() -> None:
    answer = (
        "Attention divides each dot product by $\\sqrt{d_k}$ before the softmax [S1].\n\n"
        "Without it, large dot products push the softmax into regions with tiny gradients [S1]. "
        "The model was trained on about 4.5 million sentence pairs [S2]."
    )
    picked = cited_sentences(answer, SOURCES)
    assert picked == {
        "S1": "We suspect that for large values of d_k, the dot products grow large in magnitude, pushing "
        "the softmax function into regions where it has extremely small gradients^4.",
        "S2": "We trained on the standard WMT 2014 English-German dataset consisting of about 4.5 million "
        "sentence pairs.",
    }
    # Exactly as it appears in the whitespace-collapsed passage, so the UI can find it.
    flat = " ".join(SDPA_TEXT.split())
    assert picked["S1"] in flat


def test_too_little_in_common_leaves_it_empty() -> None:
    # One shared word is not enough to bold a sentence: a wrong pick is worse than none.
    assert best_sentence(TRAINING_TEXT, content_words("A dataset is a collection.")) is None
    assert cited_sentences("Something unrelated about cats and dogs [S2].", SOURCES) == {}


def test_a_short_fragment_does_not_win_on_brevity_alone() -> None:
    passage = "Scaled dot-product attention. We scale the dot products in attention by root d_k to keep softmax gradients large."
    answer_words = content_words(
        "Attention scales the dot products by root d_k so softmax gradients stay large."
    )
    assert best_sentence(passage, answer_words).startswith("We scale")


def test_only_cited_sources_get_a_sentence_and_labels_are_unchanged() -> None:
    refs = with_cited_sentences(
        "The authors trained on WMT 2014 English-German sentence pairs [S2].", SOURCES
    )
    assert [r.label for r in refs] == ["S1", "S2", "S3"]
    assert [r.cited_sentence is not None for r in refs] == [False, True, False]
    assert refs[1].chunk_id == SOURCES[1].ref.chunk_id
    # The originals are not mutated: the earlier 'sources' event went out without the field.
    assert SOURCES[1].ref.cited_sentence is None


def test_deterministic() -> None:
    answer = "Dot products grow large, so the softmax saturates [S1]."
    assert cited_sentences(answer, SOURCES) == cited_sentences(answer, SOURCES)
