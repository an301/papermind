"""Chunking: section-first splitting, sentence-aligned cuts, runt merging, hyphens, boxes, ids."""

from __future__ import annotations

import re
from itertools import pairwise

import pytest

from papermind.ingest.chunker import (
    CHARS_PER_TOKEN,
    MIN_CHUNK_CHARS,
    Vocabulary,
    chunk_sections,
    estimate_tokens,
    join_lines,
    split_paragraphs,
)
from papermind.ingest.extract import Line, extract_pdf
from papermind.ingest.sections import SectionText, detect_sections
from papermind.schemas.paper import Section

SENTENCE = "Attention layers compare every token with every other token in the sequence."


def make_line(
    text: str,
    *,
    y: float,
    page: int = 0,
    column: int = 0,
    x0: float = 72.0,
    width: float = 230.0,
    indent: float = 0.0,
    kind: str = "body",
    float_id: int | None = None,
    size: float = 10.0,
) -> Line:
    return Line(
        text=text,
        page=page,
        x0=x0 + indent,
        y0=y,
        x1=x0 + width,
        y1=y + size,
        size=size,
        bold=False,
        font="Times-Roman",
        block=int(y),
        band=0,
        column=column,
        indent=indent,
        kind=kind,  # type: ignore[arg-type]
        float_id=float_id,
    )


def paragraph_lines(
    sentences: int, *, y: float, page: int = 0, column: int = 0, x0: float = 72.0
) -> list[Line]:
    """A paragraph of ``sentences`` sentences wrapped at ~60 characters, first line indented."""
    words = " ".join([SENTENCE] * sentences).split()
    rows: list[str] = []
    current: list[str] = []
    for word in words:
        if sum(len(w) + 1 for w in current) + len(word) > 60:
            rows.append(" ".join(current))
            current = []
        current.append(word)
    rows.append(" ".join(current))
    lines = []
    for i, row in enumerate(rows):
        width = 230.0 if i < len(rows) - 1 else 120.0  # a short last line ends the paragraph
        indent = 12.0 if i == 0 else 0.0
        lines.append(
            make_line(row, y=y + 12 * i, page=page, column=column, x0=x0, width=width, indent=indent)
        )
    return lines


def section(number: str | None, title: str, lines: list[Line], sid: str | None = None) -> SectionText:
    return SectionText(
        Section(
            section_id=sid or (f"sec_{number}" if number else f"sec_{title.lower()}"),
            number=number,
            title=title,
            page=0,
        ),
        lines,
    )


def test_estimate_tokens_is_chars_over_four() -> None:
    assert CHARS_PER_TOKEN == 4
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_a_section_that_fits_is_one_chunk() -> None:
    lines = paragraph_lines(3, y=100) + paragraph_lines(2, y=200)
    [chunk] = chunk_sections("2401.00001", [section("3.2.1", "Scaled Dot-Product Attention", lines)], 600)
    assert chunk.chunk_id == "2401.00001:sec_3.2.1_scaled_dot_product_attention"
    assert chunk.part is None
    assert chunk.section_id == "sec_3.2.1" and chunk.section_number == "3.2.1"
    assert chunk.text.count("\n\n") == 1  # two paragraphs
    assert chunk.token_count == estimate_tokens(chunk.text)


def test_long_sections_split_into_balanced_parts() -> None:
    lines: list[Line] = []
    y = 80.0
    for _ in range(8):
        para = paragraph_lines(6, y=y)
        lines += para
        y = para[-1].y1 + 14
    chunks = chunk_sections("p", [section("4", "Experiments", lines)], 300)
    ids = [c.chunk_id for c in chunks]
    assert ids == [f"p:sec_4_experiments_p{i}" for i in range(1, len(chunks) + 1)]
    assert len(chunks) >= 3
    assert [c.part for c in chunks] == list(range(1, len(chunks) + 1))
    sizes = [len(c.text) for c in chunks]
    assert max(sizes) <= 300 * CHARS_PER_TOKEN + MIN_CHUNK_CHARS
    assert max(sizes) - min(sizes) < 0.6 * max(sizes)  # packed evenly, no runt at the end
    # Nothing lost, nothing duplicated: every paragraph appears exactly once.
    joined = "\n\n".join(c.text for c in chunks)
    assert joined.count(SENTENCE) == 48


def test_one_long_paragraph_splits_at_sentence_boundaries() -> None:
    text_sentences = 40
    lines = paragraph_lines(text_sentences, y=80)
    chunks = chunk_sections("p", [section("2", "Method", lines)], 200)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.text.startswith("Attention layers")
        assert chunk.text.endswith("sequence.")
    assert sum(c.text.count(SENTENCE) for c in chunks) == text_sentences


def test_abbreviations_do_not_end_sentences() -> None:
    text = ("Prior work (Vaswani et al. 2017) used it, e.g. for translation. " * 30).strip()
    lines = [make_line(text, y=80, width=230.0)]
    chunks = chunk_sections("p", [section("1", "Intro", lines)], 150)
    for chunk in chunks:
        assert not chunk.text.endswith(("et al.", "e.g."))


def test_runts_merge_into_a_neighbour() -> None:
    big = paragraph_lines(14, y=80)
    tiny = paragraph_lines(1, y=big[-1].y1 + 14)
    chunks = chunk_sections("p", [section("5", "Results", big + tiny)], 300)
    assert all(len(c.text) >= MIN_CHUNK_CHARS for c in chunks)
    assert chunks[-1].text.endswith(SENTENCE)


def test_sections_never_share_a_chunk_and_empty_sections_have_none() -> None:
    sections = [
        section("1", "Introduction", paragraph_lines(1, y=80)),
        section("2", "Background", []),
        section("3", "Method", paragraph_lines(1, y=200)),
    ]
    chunks = chunk_sections("p", sections, 600)
    assert [c.section_id for c in chunks] == ["sec_1", "sec_3"]


def test_repeated_titles_get_distinct_ids() -> None:
    sections = [
        section(None, "Discussion", paragraph_lines(2, y=80), sid="sec_discussion"),
        section(None, "Discussion", paragraph_lines(2, y=200), sid="sec_discussion_2"),
    ]
    ids = [c.chunk_id for c in chunk_sections("p", sections, 600)]
    assert ids == ["p:sec_discussion", "p:sec_discussion_2"]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (["a learned repre-", "sentation of tokens"], "a learned representation of tokens"),
        (["the self-", "attention layer"], "the self-attention layer"),
        (["we use GPT-", "3 and Bi-", "LSTM models"], "we use GPT-3 and Bi-LSTM models"),
        (["trained end-", "to-end with SGD"], "trained end-to-end with SGD"),
        (["see https://github.com/", "google/bert for code"], "see https://github.com/google/bert for code"),
        (["a long pause—", "then more"], "a long pause—then more"),
        (["first line", "second line"], "first line second line"),
    ],
)
def test_join_lines_repairs_line_end_hyphens(rows: list[str], expected: str) -> None:
    lines = [make_line(r, y=80 + 12 * i) for i, r in enumerate(rows)]
    text, ranges = join_lines(rows, Vocabulary(lines))
    assert text == expected
    # Each line maps onto exactly its own text: a dropped line-end hyphen leaves
    # its line's range, and no range reaches into the next line.
    for (a, b), row in zip(ranges, rows, strict=True):
        assert text[a:b] in (row, row[:-1])
    assert all(b <= a2 for (_, b), (a2, _) in pairwise(ranges))


def test_hyphen_decision_uses_the_papers_own_spelling() -> None:
    corpus = [
        make_line(t, y=80 + 12 * i) for i, t in enumerate(["pre-training helps", "and multiplicative noise"])
    ]
    vocab = Vocabulary(corpus)
    assert vocab.keep_hyphen("pre", "training")  # the paper writes 'pre-training'
    assert not vocab.keep_hyphen("multi", "plicative")  # ...and 'multiplicative'
    assert not vocab.keep_hyphen("atten", "tion")  # a word ending is never a word
    # TeX never breaks right after '-ly', so an adverb at a line end is a
    # compound even when neither half appears elsewhere in the paper.
    assert vocab.keep_hyphen("greedily", "sampled")
    assert vocab.keep_hyphen("Deeply", "supervised")
    assert not vocab.keep_hyphen("apply", "ing")  # ...but a suffix is still TeX's break


def test_paragraphs_continue_across_a_column_break() -> None:
    first = paragraph_lines(3, y=600)
    # The paragraph runs off the bottom of the left column mid-sentence...
    first[-1] = make_line("and the rest of this sentence continues in the", y=first[-1].y0, width=230.0)
    second = [make_line("next column without a break.", y=80, column=1, x0=320.0, width=150.0)]
    paragraphs = split_paragraphs(first + second, Vocabulary(first + second))
    assert len(paragraphs) == 1
    assert paragraphs[0].text.endswith("continues in the next column without a break.")


def test_boxes_merge_lines_per_column_and_page() -> None:
    left = paragraph_lines(3, y=600)
    right = paragraph_lines(3, y=80, column=1, x0=320.0)
    next_page = paragraph_lines(2, y=80, page=1)
    [chunk] = chunk_sections("p", [section("1", "Intro", left + right + next_page)], 2000)
    # One box per column per page, spanning the indented first line and the rest.
    assert [(b.page, round(b.x0)) for b in chunk.boxes] == [(0, 72), (0, 320), (1, 72)]
    first = chunk.boxes[0]
    assert first.y0 == pytest.approx(600) and first.y1 == pytest.approx(left[-1].y1)
    assert chunk.page_start == 0 and chunk.page_end == 1


def test_synthetic_paper_chunks(two_column_pdf: bytes) -> None:
    document = extract_pdf(two_column_pdf)
    chunks = chunk_sections("2401.00001", detect_sections(document.lines, document.body_size), 600)
    by_id = {c.chunk_id.split(":", 1)[1]: c for c in chunks}
    assert list(by_id) == [
        "sec_front_matter",
        "sec_abstract",
        "sec_1_introduction",
        "sec_2_method",
        "sec_2.1_scaled_attention",
        "sec_3_experiments",
        "sec_4_conclusion",
        "sec_references",
    ]
    intro = by_id["sec_1_introduction"].text
    assert "a representation of every token" in intro
    assert "We use self-attention throughout" in intro
    # The figure caption interrupted the paragraph on the page; in the chunk it comes last.
    assert intro.endswith("Figure 1: Overview of the synthetic pipeline.")
    assert "Table 1: Accuracy of each model.\n\nModel Accuracy 3.3 28.4" in by_id["sec_3_experiments"].text
    for chunk in chunks:
        assert chunk.boxes, chunk.chunk_id
        assert all(0 <= b.x0 < b.x1 <= 612 and 0 <= b.y0 < b.y1 <= 792 for b in chunk.boxes)
        assert not re.search(r"\b(Preprint|arXiv:2401)\b", chunk.text)
