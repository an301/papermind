"""Chunk and section ids: stable, readable, and never positional."""

from __future__ import annotations

import pytest

from papermind.ingest.ids import CHUNKER_VERSION, chunk_id, disambiguate, section_id, section_key, slugify


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Scaled Dot-Product Attention", "scaled_dot_product_attention"),
        ("Aren’t Existing Solutions Good Enough?", "arent_existing_solutions_good_enough"),
        ("Classiﬁcation", "classification"),  # ligature in the PDF, plain in the id
        ("Täckström’s Résumé", "tackstroms_resume"),
        ("Comparison of BERT, ELMo, and OpenAI GPT", "comparison_of_bert_elmo_and_openai"),
        ("How Does the Adaptation Matrix ∆W Compare to W?", "how_does_the_adaptation_matrix_w"),
        ("∑∏", "untitled"),
    ],
)
def test_slugify(title: str, slug: str) -> None:
    assert slugify(title) == slug


def test_slugs_are_capped_on_a_word_boundary() -> None:
    slug = slugify("Deriving the Optimum of the KL-Constrained Reward Maximization Objective")
    assert slug == "deriving_the_optimum_of_the_kl"
    long_words = slugify("Supercalifragilisticexpialidocious Antidisestablishmentarianism Words")
    assert len(long_words) <= 48 and not long_words.endswith("_")


def test_section_ids_and_keys() -> None:
    assert section_id("3.2.1", "Scaled Dot-Product Attention") == "sec_3.2.1"
    assert section_id(None, "Abstract") == "sec_abstract"
    assert section_key("3.2.1", "Scaled Dot-Product Attention") == "sec_3.2.1_scaled_dot_product_attention"
    assert section_key("A.1", "Illustration") == "sec_A.1_illustration"
    assert section_key(None, "References") == "sec_references"


def test_chunk_ids_follow_the_documented_format() -> None:
    key = section_key("3.2.1", "Scaled Dot-Product Attention")
    assert chunk_id("1706.03762", key) == "1706.03762:sec_3.2.1_scaled_dot_product_attention"
    assert chunk_id("1706.03762", "sec_references", 2) == "1706.03762:sec_references_p2"


def test_repeats_are_disambiguated_in_document_order() -> None:
    keys = ["sec_discussion", "sec_1_intro", "sec_discussion", "sec_discussion"]
    assert disambiguate(keys) == ["sec_discussion", "sec_1_intro", "sec_discussion_2", "sec_discussion_3"]


def test_chunker_version_is_a_string_written_into_records() -> None:
    assert isinstance(CHUNKER_VERSION, str) and CHUNKER_VERSION
