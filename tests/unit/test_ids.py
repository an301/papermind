"""ID construction. These IDs are eval labels; every case here is a promise."""

import pytest
from papermind.ingest.ids import chunk_id, disambiguate, section_key, slugify


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Attention", "attention"),
        ("Scaled Dot-Product Attention", "scaled_dot_product_attention"),
        ("Position-wise Feed-Forward Networks", "position_wise_feed_forward_networks"),
        ("Why Self-Attention?", "why_self_attention"),
        # Ligatures and accents must fold, or the same section gets two IDs
        # depending on how the PDF encoded it.
        ("Classiﬁcation", "classification"),
        ("Naïve Bayes", "naive_bayes"),
        # Trailing footnote markers and stray punctuation are not identity.
        ("Conclusion.", "conclusion"),
        ("Results †", "results"),
        ("", "untitled"),
        ("!!!", "untitled"),
    ],
)
def test_slugify(title: str, expected: str) -> None:
    assert slugify(title) == expected


def test_slug_is_word_bounded_when_truncated() -> None:
    title = "Extremely Long Section Title That Keeps Going And Going Forever"
    slug = slugify(title)
    assert len(slug) <= 48
    assert not slug.endswith("_")
    # Truncation must land on a word boundary, never mid-word.
    words = set(title.lower().split())
    assert all(part in words for part in slug.split("_"))


def test_section_key_shapes() -> None:
    assert section_key("3.2", "Attention") == "sec_3.2_attention"
    assert section_key(None, "Abstract") == "sec_abstract"
    assert section_key("A.1", "Proofs") == "sec_A.1_proofs"


def test_chunk_id_matches_the_documented_format() -> None:
    assert chunk_id("1706.03762", "sec_3.2_attention") == "1706.03762:sec_3.2_attention"


def test_unsplit_sections_carry_no_part_suffix() -> None:
    assert chunk_id("1706.03762", "sec_7_conclusion", part=1, n_parts=1).endswith("conclusion")


def test_split_sections_are_numbered_from_one() -> None:
    ids = [chunk_id("1706.03762", "sec_references", part=i, n_parts=3) for i in (1, 2, 3)]
    assert ids == [
        "1706.03762:sec_references_p1",
        "1706.03762:sec_references_p2",
        "1706.03762:sec_references_p3",
    ]


def test_disambiguate_is_document_ordered() -> None:
    assert disambiguate(["sec_setup", "sec_results", "sec_setup", "sec_setup"]) == [
        "sec_setup",
        "sec_results",
        "sec_setup_2",
        "sec_setup_3",
    ]


def test_ids_do_not_depend_on_position() -> None:
    """Inserting a section must not renumber the others -- the whole point."""
    before = section_key("3.2", "Attention")
    after = section_key("3.2", "Attention")
    assert before == after == "sec_3.2_attention"
