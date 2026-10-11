"""Heading detection: numbering + typography + sequence, the outline, and false headings."""

from __future__ import annotations

import pytest

from papermind.ingest.extract import Line, OutlineEntry, extract_pdf
from papermind.ingest.sections import detect_sections

BODY = 10.0


class Page:
    """Lays out synthetic rows top to bottom, like a single-column page."""

    def __init__(self) -> None:
        self.lines: list[Line] = []
        self.y = 72.0
        self.page = 0
        self.block = 0

    def row(
        self,
        text: str,
        *,
        size: float = BODY,
        bold: bool = False,
        kind: str = "body",
        float_id: int | None = None,
        gap: float = 2.0,
        new_block: bool = True,
    ) -> Line:
        if new_block:
            self.block += 1
        line = Line(
            text=text,
            page=self.page,
            x0=72.0,
            y0=self.y,
            x1=72.0 + 5.0 * len(text),
            y1=self.y + size,
            size=size,
            bold=bold,
            font="Times-Bold" if bold else "Times-Roman",
            block=self.block,
            band=0,
            column=0,
            kind=kind,  # type: ignore[arg-type]
            float_id=float_id,
        )
        self.lines.append(line)
        self.y += size + gap
        return line

    def heading(self, text: str, size: float = 12.0) -> Line:
        self.y += 8
        return self.row(text, size=size, bold=True)

    def para(
        self, text: str = "Body text of a paragraph that runs across the column.", rows: int = 2
    ) -> None:
        for i in range(rows):
            self.row(text, new_block=i == 0)

    def next_page(self) -> None:
        self.page += 1
        self.y = 72.0


def outline(lines: list[Line], entries: list[OutlineEntry] | None = None) -> list[tuple[str, str]]:
    return [(s.section.section_id, s.section.title) for s in detect_sections(lines, BODY, entries or [])]


def test_synthetic_paper_outline(two_column_pdf: bytes) -> None:
    document = extract_pdf(two_column_pdf)
    sections = detect_sections(document.lines, document.body_size, document.outline)
    assert [(s.section.section_id, s.section.title, s.section.level) for s in sections] == [
        ("sec_front_matter", "Front matter", 1),
        ("sec_abstract", "Abstract", 1),
        ("sec_1", "Introduction", 1),
        ("sec_2", "Method", 1),
        ("sec_2.1", "Scaled Attention", 2),
        ("sec_3", "Experiments", 1),
        ("sec_4", "Conclusion", 1),
        ("sec_references", "References", 1),
    ]
    method = sections[3].section
    assert method.page == 0 and method.y == pytest.approx(137, abs=2)
    assert sections[5].section.page == 1
    # The table row '3.3 28.4' is bold and numbered, and is still not section 3.3.
    assert all(s.section.number != "3.3" for s in sections)


def test_numbered_and_bare_number_headings() -> None:
    p = Page()
    p.heading("1 Introduction")
    p.para()
    p.heading("2")
    p.row("Background", size=12.0, bold=True, gap=2)
    p.para()
    p.heading("2.1 Prior Work", size=BODY)
    p.para()
    assert outline(p.lines) == [("sec_1", "Introduction"), ("sec_2", "Background"), ("sec_2.1", "Prior Work")]


def test_false_headings_are_rejected() -> None:
    p = Page()
    p.heading("1 Introduction")
    p.para()
    p.heading("2 Training")
    p.heading("2.1 Regularization", size=BODY)
    p.para()
    p.row("1 GPU was used for every run", bold=True)  # out of sequence after 2.1
    p.row("3.3 28.4 41.8 26.0", bold=True)  # a table row
    p.row("(3)", bold=True)  # an equation number
    p.row("Figure 2: Results on the test set", bold=True)  # a caption
    p.row("1 epoch of warm-up was enough", size=BODY)  # numbered prose, not styled
    p.para()
    assert outline(p.lines) == [
        ("sec_1", "Introduction"),
        ("sec_2", "Training"),
        ("sec_2.1", "Regularization"),
    ]


def test_unnumbered_named_headings() -> None:
    p = Page()
    p.heading("Abstract")
    p.para()
    p.heading("Introduction")
    p.para()
    p.heading("Acknowledgements")
    p.para()
    p.heading("References")
    p.para("[1] A. Author. A paper. 2020.", rows=1)
    assert outline(p.lines) == [
        ("sec_abstract", "Abstract"),
        ("sec_introduction", "Introduction"),
        ("sec_acknowledgements", "Acknowledgements"),
        ("sec_references", "References"),
    ]


def test_run_in_abstract() -> None:
    p = Page()
    p.row("Abstract—We propose a new method for testing heading detection.", bold=False)
    p.para()
    sections = detect_sections(p.lines, BODY, [])
    assert sections[0].section.title == "Abstract"
    assert sections[0].lines[0].text.startswith("We propose a new method")


def test_lettered_appendix_after_references() -> None:
    p = Page()
    p.heading("1 Introduction")
    p.para()
    p.heading("References")
    p.para("[1] A. Author. A paper. 2020.", rows=1)
    p.next_page()
    p.heading("A Proofs of the Main Results")
    p.para()
    p.heading("A.1 Proof of Lemma 1", size=BODY)
    p.para()
    p.heading("B Hyperparameters")
    p.para()
    assert outline(p.lines) == [
        ("sec_1", "Introduction"),
        ("sec_references", "References"),
        ("sec_A", "Proofs of the Main Results"),
        ("sec_A.1", "Proof of Lemma 1"),
        ("sec_B", "Hyperparameters"),
    ]


def test_a_title_starting_with_a_is_not_appendix_a() -> None:
    p = Page()
    p.row("A Simple Framework for Contrastive Learning", size=17.0, bold=True)
    p.row("Ada Lovelace", size=11.0)
    p.heading("1 Introduction")
    p.para()
    p.heading("2 Method")
    p.para()
    assert outline(p.lines) == [
        ("sec_front_matter", "Front matter"),
        ("sec_1", "Introduction"),
        ("sec_2", "Method"),
    ]


def test_wrapped_heading_rows_are_joined() -> None:
    p = Page()
    p.heading("1 Introduction")
    p.para()
    p.heading("2 Learning Rate Schedules for Very")
    p.row("Deep Residual Networks", size=12.0, bold=True, gap=2)
    p.para()
    assert outline(p.lines)[1] == ("sec_2", "Learning Rate Schedules for Very Deep Residual Networks")


def test_outline_bookmarks_place_numbered_headings() -> None:
    p = Page()
    p.row("Abstract", bold=True)
    p.para()
    # Set at body size and weight: typography alone would not call these headings.
    p.row("1 Introduction")
    p.para()
    p.next_page()
    p.row("2 Our Method for ∆W Updates")
    p.para()
    entries = [
        OutlineEntry(1, "Introduction", 0),
        OutlineEntry(1, "Our Method for W Updates", 1),  # hyperref drops the math
        OutlineEntry(1, "Conclusion", 1),  # not in the text: skipped
    ]
    assert outline(p.lines, entries) == [
        ("sec_abstract", "Abstract"),
        ("sec_1", "Introduction"),
        ("sec_2", "Our Method for ∆W Updates"),
    ]


def test_repeated_titles_are_disambiguated_in_order() -> None:
    p = Page()
    p.heading("1 Introduction")
    p.para()
    p.heading("Discussion")
    p.para()
    p.heading("Discussion")
    p.para()
    ids = [sid for sid, _ in outline(p.lines)]
    assert ids == ["sec_1", "sec_discussion", "sec_discussion_2"]


def test_floats_and_footnotes_move_to_the_end_of_their_section() -> None:
    p = Page()
    p.heading("1 Introduction")
    p.row("First half of a sentence that continues")
    p.row("Figure 1: A caption between two halves.", kind="caption", float_id=7)
    p.row("Footnote text set at the bottom of the column.", kind="footnote", float_id=10_000_001)
    p.row("Table 1: Accuracy.", kind="caption", float_id=8)
    p.row("Model 28.4", kind="table", float_id=8)
    p.row("after the float, in the same paragraph.", new_block=False)
    sections = detect_sections(p.lines, BODY, [])
    assert [line.text for line in sections[0].lines] == [
        "First half of a sentence that continues",
        "after the float, in the same paragraph.",
        "Figure 1: A caption between two halves.",
        "Table 1: Accuracy.",
        "Model 28.4",
        "Footnote text set at the bottom of the column.",
    ]


def footnote(p: Page, text: str, note_id: int) -> Line:
    line = p.row(text, size=8.0, kind="footnote", float_id=note_id)
    line.leading_marker = True
    return line


def test_a_footnote_goes_to_the_section_that_cites_it() -> None:
    # The footnote is set at the bottom of the page, under the next heading in
    # reading order; it belongs to the section whose text carries its mark.
    p = Page()
    p.heading("3.2.1 Scaled Dot-Product Attention")
    p.row("pushing the softmax into regions with extremely small gradients^4. To counteract")
    p.heading("3.2.2 Multi-Head Attention", size=BODY)
    p.row("Instead of performing a single attention function we project h times.")
    footnote(p, "4 To illustrate why the dot products get large, assume unit variance.", 10_000_001)
    sections = detect_sections(p.lines, BODY, [])
    by_id = {s.section.section_id: [line.text for line in s.lines] for s in sections}
    assert by_id["sec_3.2.1"][-1].startswith("4 To illustrate")
    assert not any(text.startswith("4 To illustrate") for text in by_id["sec_3.2.2"])


def test_stacked_author_marks_and_whole_numbers_decide_footnote_homes() -> None:
    p = Page()
    p.row("Ashish Vaswani^{∗} Aidan N. Gomez^{∗†}")
    p.heading("Abstract")
    p.row("We propose the Transformer, tested on 2^{14} tokens per batch.")
    footnote(p, "† Work performed while at Google Brain.", 10_000_001)
    # '^{14}' is not a citation of footnote 1, so footnote 1 stays where it is.
    footnote(p, "1 A footnote nobody on this page cites.", 10_000_002)
    sections = detect_sections(p.lines, BODY, [])
    assert [line.text for line in sections[0].lines][-1] == "† Work performed while at Google Brain."
    assert [line.text for line in sections[1].lines][-1] == "1 A footnote nobody on this page cites."


def test_empty_sections_stay_in_the_outline() -> None:
    p = Page()
    p.heading("1 Experiments")
    p.heading("1.1 Setup", size=BODY)
    p.para()
    sections = detect_sections(p.lines, BODY, [])
    assert [s.section.section_id for s in sections] == ["sec_1", "sec_1.1"]
    assert sections[0].is_empty and not sections[1].is_empty
