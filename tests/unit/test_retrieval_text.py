"""The shared lexical tokenizer and what each chunk is indexed as."""

import pytest

from papermind.retrieval.text import indexable_text, tokenize
from papermind.schemas.paper import Chunk


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Scaled Dot-Product Attention", ["scaled", "dot-product", "dot", "product", "attention"]),
        ("divide by d_k", ["divide", "by", "d_k"]),
        ("GPT-3 and BERT-base", ["gpt-3", "gpt", "and", "bert-base", "bert", "base"]),
        ("an L2 penalty", ["an", "l2", "penalty"]),
        ("28.4 BLEU", ["28.4", "28", "bleu"]),
        ("the model's output", ["the", "model's", "model", "output"]),
    ],
)
def test_notation_survives_whole(text, expected):
    assert tokenize(text) == expected


def test_case_ligatures_and_full_width_forms_fold():
    assert tokenize("ﬁne-tuning") == tokenize("fine-tuning")
    assert tokenize("ＢＥＲＴ") == ["bert"]
    assert tokenize("Attention") == tokenize("attention")


def test_light_plural_fold_only():
    assert tokenize("embeddings") == tokenize("embedding")
    assert tokenize("queries") == ["query"]
    # Not a stemmer: these stay distinct terms.
    assert tokenize("normalization") != tokenize("normal")
    assert tokenize("attention") != tokenize("attend")
    # Short words, '-us' and '-ss' words are left alone.
    assert tokenize("bus") == ["bus"]
    assert tokenize("corpus") == ["corpus"]
    assert tokenize("loss") == ["loss"]


def test_sibilant_plurals_meet_their_singular():
    # The ablation found 'batches' indexed as 'batche', so 'batch' never matched it.
    for singular, plural in [
        ("batch", "batches"),
        ("patch", "patches"),
        ("box", "boxes"),
        ("class", "classes"),
        ("loss", "losses"),
        ("approach", "approaches"),
        ("cache", "caches"),
    ]:
        assert tokenize(singular) == tokenize(plural), (singular, plural)
    assert tokenize("mini-batches") == ["mini-batches", "mini", "batch"]
    # '-e' words keep Harman's rule.
    for singular, plural in [("response", "responses"), ("case", "cases"), ("image", "images")]:
        assert tokenize(singular) == tokenize(plural), (singular, plural)


def test_acronym_plurals_fold_to_the_acronym():
    assert tokenize("8 P100 GPUs") == ["8", "p100", "gpu"]
    assert tokenize("LLMs and RNNs") == ["llm", "and", "rnn"]
    assert tokenize("GPU") == ["gpu"]


def test_sentence_punctuation_is_not_part_of_a_token():
    assert tokenize("We use Adam. Then we stop.") == ["we", "use", "adam", "then", "we", "stop"]


def test_no_stopword_list():
    # 'a' in 'Appendix A' and 'it' are kept; IDF discounts common words instead.
    assert tokenize("Appendix A: it works") == ["appendix", "a", "it", "work"]


def test_query_and_chunk_tokenize_identically():
    chunk = "For large values of d_k, the dot products grow large in magnitude."
    query = "why does a large d_k make dot products grow"
    shared = set(tokenize(chunk)) & set(tokenize(query))
    assert {"d_k", "dot", "product", "grow", "large"} <= shared


def _chunk(number: str | None, title: str, text: str) -> Chunk:
    return Chunk(
        chunk_id="p:sec_x",
        paper_id="p",
        section_id="sec_x",
        section_number=number,
        section_title=title,
        text=text,
        token_count=1,
        page_start=0,
        page_end=0,
    )


def test_indexable_text_prepends_the_heading():
    assert indexable_text(_chunk("5.2", "Hardware and Schedule", "We trained on 8 GPUs.")) == (
        "5.2 Hardware and Schedule\nWe trained on 8 GPUs."
    )
    assert indexable_text(_chunk(None, "Abstract", "We propose.")) == "Abstract\nWe propose."
