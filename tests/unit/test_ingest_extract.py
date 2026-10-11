"""PDF extraction: reading order, page furniture, scripts, floats, and the failure modes."""

from __future__ import annotations

import unicodedata

import pymupdf
import pytest

from papermind.ingest.extract import (
    EncryptedPDF,
    ExtractedDocument,
    NoTextPDF,
    PDFError,
    _clean,
    _fold_accents,
    _Frag,
    _render_row,
    _Span,
    caption_kind,
    extract_pdf,
)


@pytest.fixture(scope="module")
def document(two_column_pdf: bytes) -> ExtractedDocument:
    return extract_pdf(two_column_pdf)


def texts(document: ExtractedDocument) -> list[str]:
    return [line.text for line in document.lines]


def index_of(document: ExtractedDocument, prefix: str) -> int:
    return next(i for i, line in enumerate(document.lines) if line.text.startswith(prefix))


def test_basic_shape(document: ExtractedDocument) -> None:
    assert document.num_pages == 2
    assert document.page_sizes == [(612.0, 792.0), (612.0, 792.0)]
    assert document.body_size == pytest.approx(10.0, abs=0.3)


def test_two_columns_are_read_left_then_right(document: ExtractedDocument) -> None:
    order = [
        index_of(document, "Synthetic Layouts"),
        index_of(document, "Abstract"),
        index_of(document, "1 Introduction"),
        index_of(document, "After the figure"),
        index_of(document, "2 Method"),
        index_of(document, "2.1 Scaled Attention"),
        index_of(document, "3 Experiments"),
        index_of(document, "4 Conclusion"),
        index_of(document, "References"),
    ]
    assert order == sorted(order)
    left = document.lines[index_of(document, "1 Introduction")]
    right = document.lines[index_of(document, "2 Method")]
    assert (left.column, right.column) == (0, 1)
    # No row mixes the two columns' text.
    assert not any("Method" in t and "Introduction" in t for t in texts(document))


def test_running_heads_page_numbers_and_stamp_are_dropped(document: ExtractedDocument) -> None:
    joined = "\n".join(texts(document))
    assert "Preprint" not in joined
    assert "arXiv:2401" not in joined
    assert not any(t.strip() in {"1", "2"} for t in texts(document))


def test_scripts_are_kept_and_marked(document: ExtractedDocument) -> None:
    joined = "\n".join(texts(document))
    assert "O(n^2)" in joined
    assert "square root of d_k" in joined


def test_floats_are_flagged(document: ExtractedDocument) -> None:
    figure = document.lines[index_of(document, "Figure 1:")]
    table = document.lines[index_of(document, "Table 1:")]
    assert figure.kind == "caption" and table.kind == "caption"
    rows = [line for line in document.lines if line.float_id == table.float_id and line.kind == "table"]
    assert [r.text for r in rows] == ["Model Accuracy", "3.3 28.4"]
    # The paragraph after the table is prose again.
    assert document.lines[index_of(document, "The equation above")].kind == "body"


def test_headings_carry_typography(document: ExtractedDocument) -> None:
    heading = document.lines[index_of(document, "1 Introduction")]
    body = document.lines[index_of(document, "Dense models")]
    assert heading.bold and heading.size == pytest.approx(12.0)
    assert not body.bold and body.size == pytest.approx(10.0)
    assert body.page == 0 and 0 < body.x0 < body.x1 and 0 < body.y0 < body.y1


def test_outline_is_read_when_present() -> None:
    doc = pymupdf.open()
    for title in ("Introduction", "Method"):
        page = doc.new_page()
        page.insert_text((72, 72), title, fontsize=14, fontname="tibo")
        page.insert_text((72, 100), "Body text that is long enough to count as text. " * 4, fontsize=10)
    doc.set_toc([[1, "1 Introduction", 1], [1, "2 Method", 2], [2, "2.1 Detail", -1]])
    document = extract_pdf(doc.tobytes())
    assert [(e.level, e.title, e.page) for e in document.outline] == [
        (1, "1 Introduction", 0),
        (1, "2 Method", 1),
    ]


def test_encrypted_pdf_is_refused() -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret " * 100, fontsize=10)
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    with pytest.raises(EncryptedPDF, match="password"):
        extract_pdf(locked)


def test_scanned_pdf_without_text_is_refused() -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(50, 50, 500, 700), color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    with pytest.raises(NoTextPDF, match=r"^This PDF has no selectable text"):
        extract_pdf(doc.tobytes())


def test_garbage_is_not_a_pdf() -> None:
    with pytest.raises(PDFError):
        extract_pdf(b"%PDF-1.4 this is not really a pdf at all")


def test_ligatures_and_math_letters_fold() -> None:
    assert _clean("classiﬁcation is eﬃcient") == "classification is efficient"
    assert _clean("\U0001d465 + \U0001d466") == "x + y"
    # A zero-width space marks a word break; control characters are blanked.
    assert _clean("zero​width\x00") == "zero width "


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("T¨ackstr¨om", "Täckström"),
        ("G¨ulc¸ehre", "Gülçehre"),
        ("Lo¨ıc", "Loïc"),
        ("Luˇci´c", "Lučić"),
        ("H´enaff", "Hénaff"),
        ("plain words", "plain words"),
    ],
)
def test_detached_accents_rejoin_their_letters(raw: str, expected: str) -> None:
    assert _fold_accents(unicodedata.normalize("NFKC", raw)) == expected


def _span(text: str, x0: float, x1: float, *, size: float = 10.0, baseline: float = 100.0) -> _Span:
    return _Span(text=text, size=size, bold=False, font="Times", x0=x0, x1=x1, baseline=baseline, flags=0)


def _frag(spans: list[_Span]) -> _Frag:
    return _Frag(
        spans=spans,
        x0=spans[0].x0,
        y0=90.0,
        x1=spans[-1].x1,
        y1=102.0,
        block=1,
        baseline=100.0,
        size=10.0,
    )


def test_whitespace_spans_are_joined_not_dropped() -> None:
    # LaTeX PDFs often emit each inter-word space as its own span; filtering
    # whitespace-only spans glues the words together.
    row = [_frag([_span("Hello", 0, 20), _span(" ", 20, 23), _span("world", 23, 45)])]
    assert _render_row(row, 10.0).text == "Hello world"


def test_separate_fragments_get_a_space_across_a_gap() -> None:
    row = [_frag([_span("3.2", 0, 12)]), _frag([_span("Attention", 20, 60)])]
    assert _render_row(row, 10.0).text == "3.2 Attention"


def test_sub_and_superscripts_are_marked() -> None:
    row = [
        _frag(
            [
                _span("d", 0, 5),
                _span("k", 5, 8, size=7.0, baseline=102.0),
                _span(" and n", 8, 30),
                _span("2", 30, 34, size=7.0, baseline=96.0),
            ]
        )
    ]
    assert _render_row(row, 10.0).text == "d_k and n^2"


def test_a_footnote_mark_after_a_space_still_marks_its_word() -> None:
    # TeX often leaves a (script-size) space before \footnote; the mark is
    # still the word's, and only marked ('^4') can it be matched to its footnote.
    row = [
        _frag(
            [
                _span("small gradients", 0, 70),
                _span(" ", 70, 72.5, size=7.0),
                _span("4", 72.5, 76, size=7.0, baseline=96.4),
                _span(". To counteract", 76.5, 140),
            ]
        )
    ]
    assert _render_row(row, 10.0).text == "small gradients^4. To counteract"


def test_a_fraction_numerator_a_word_space_away_is_not_a_mark() -> None:
    # Inline \frac{1}{\sqrt{d_k}}: the numerator is a raised small digit too,
    # but it stands a word space from the preceding word, not against it.
    row = [
        _frag([_span("the dot products by", 0, 90)]),
        _frag([_span("1", 99.3, 103.2, size=7.0, baseline=96.0)]),
    ]
    assert _render_row(row, 10.0).text == "the dot products by 1"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Figure 2: The architecture.", "figure"),
        ("Fig. 3. Results on ImageNet.", "figure"),
        ("Table 1: Accuracy.", "table"),
        ("Algorithm 1: Adam, our proposed algorithm", "algorithm"),
        ("Table 2 summarizes our results.", None),
        ("Figures show that", None),
    ],
)
def test_caption_kind(text: str, kind: str | None) -> None:
    assert caption_kind(text) == kind


def _figure_captioned_table(builder, *, framed: bool) -> bytes:
    """Prose, a four-column table of short cells, then 'Figure 2:' under it.

    ``framed`` draws a box around the cells, which makes them a figure's labels.
    """
    builder.new_page()
    y = builder.paragraph(72, 90, "We compare the models in the figure below. " * 6, chars=90)
    y += 20
    top = y - 12
    for cells in (
        ("Model", "Steps", "Top-1", "Top-5"),
        ("Baseline", "31.0", "72.2%", "6.67%"),
        ("Ours", "2.1", "74.8%", "4.9%"),
    ):
        for column, cell in enumerate(cells):
            builder.text(110 + 110 * column, y, cell, size=9)
        y += 12
    if framed:
        builder.rect(100, top, 540, y - 6)
    y += 14
    builder.text(72, y, "Figure 2: Steps to reach the baseline accuracy, and the best accuracy.", size=9)
    builder.paragraph(72, y + 30, "After the figure the text goes on with more prose. " * 6, chars=90)
    return builder.bytes()


def test_a_text_table_captioned_as_a_figure_is_kept(pdf_builder) -> None:
    document = extract_pdf(_figure_captioned_table(pdf_builder, framed=False))
    caption = document.lines[index_of(document, "Figure 2:")]
    rows = [ln.text for ln in document.lines if ln.float_id == caption.float_id and ln.kind == "table"]
    assert rows == ["Model Steps Top-1 Top-5", "Baseline 31.0 72.2% 6.67%", "Ours 2.1 74.8% 4.9%"]


def test_labels_inside_a_drawn_figure_are_dropped(pdf_builder) -> None:
    document = extract_pdf(_figure_captioned_table(pdf_builder, framed=True))
    assert document.lines[index_of(document, "Figure 2:")].kind == "caption"
    assert not any(line.text.startswith(("Model", "Baseline", "Ours")) for line in document.lines)
