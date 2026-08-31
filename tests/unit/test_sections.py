"""Heading detection, including the table rows that look exactly like headings."""

from papermind.ingest.models import Line
from papermind.ingest.sections import split_into_sections

BODY = 10.0


def line(
    text: str,
    *,
    size: float = BODY,
    bold: bool = False,
    page: int = 1,
    y: float = 0.0,
) -> Line:
    return Line(text=text, size=size, bold=bold, page=page, y=y, x0=100.0)


def body_block(n: int = 40, page: int = 1) -> list[Line]:
    # Enough body text that body_font_size() resolves to BODY.
    return [line(f"Body sentence number {i} with enough text to weigh the histogram.",
                 page=page, y=float(i)) for i in range(n)]


def test_numbered_heading_with_bold_is_detected() -> None:
    lines = [line("3.2 Attention", bold=True, size=12.0), *body_block()]
    sections = split_into_sections(lines)
    assert (sections[0].number, sections[0].title) == ("3.2", "Attention")


def test_numbered_line_without_heading_typography_is_body_text() -> None:
    """'3.2 shows that ...' inside a paragraph is not a heading."""
    lines = [*body_block(), line("3.2 shows that the model converges faster than the baseline.")]
    sections = split_into_sections(lines)
    assert len(sections) == 1
    assert sections[0].title == "frontmatter"


def test_bold_table_row_is_not_a_heading() -> None:
    """The exact false positive from the Transformer paper's results table."""
    lines = [*body_block(), line("3.3 · 1018", bold=True), line("28.4", bold=True)]
    sections = split_into_sections(lines)
    assert [s.title for s in sections] == ["frontmatter"]


def test_bare_number_then_title_is_one_heading() -> None:
    lines = [
        *body_block(),
        line("6", bold=True, size=12.0, y=100.0),
        line("Results", bold=True, size=12.0, y=101.0),
        *[line(f"Result prose {i}", y=102.0 + i) for i in range(5)],
    ]
    sections = split_into_sections(lines)
    assert (sections[-1].number, sections[-1].title) == ("6", "Results")


def test_bare_number_followed_by_numeric_line_is_not_a_heading() -> None:
    """'28.4' then '41.8' is a BLEU score pair, not section 28.4."""
    lines = [*body_block(), line("28.4", bold=True, y=100.0), line("41.8", bold=True, y=101.0)]
    sections = split_into_sections(lines)
    assert [s.title for s in sections] == ["frontmatter"]


def test_unnumbered_headings_are_detected_without_typography() -> None:
    lines = [*body_block(), line("References", y=100.0), *body_block(page=2)]
    sections = split_into_sections(lines)
    assert "References" in [s.title for s in sections]


def test_captions_are_never_headings() -> None:
    lines = [*body_block(), line("Table 3: Variations on the Transformer architecture.", bold=True)]
    sections = split_into_sections(lines)
    assert [s.title for s in sections] == ["frontmatter"]


def test_lettered_appendix_heading_requires_a_capitalised_title() -> None:
    accepted = split_into_sections([line("A Proofs", bold=True, size=12.0), *body_block()])
    assert (accepted[0].number, accepted[0].title) == ("A", "Proofs")

    # 'A simple observation ...' is a bold sentence, not appendix A.
    rejected = split_into_sections(
        [*body_block(), line("A simple observation about convergence", bold=True)]
    )
    assert [s.title for s in rejected] == ["frontmatter"]


def test_empty_parent_section_produces_no_chunkable_section() -> None:
    """'6 Results' followed straight by '6.1' has no prose of its own."""
    lines = [
        *body_block(),
        line("6 Results", bold=True, size=12.0, y=100.0),
        line("6.1 Machine Translation", bold=True, size=12.0, y=101.0),
        *[line(f"Translation prose {i}", y=102.0 + i) for i in range(5)],
    ]
    titles = [s.title for s in split_into_sections(lines)]
    assert "Results" not in titles
    assert "Machine Translation" in titles
