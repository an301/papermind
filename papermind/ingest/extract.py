"""PDF -> ordered, typography-annotated lines, via PyMuPDF.

Section detection downstream needs more than a flat string: it needs to know
which lines are visually headings. So this module keeps font size, boldness,
and page/column position on every line, and hands `sections.py` something it
can reason about.

Four PDF realities are handled here, each of which silently corrupts chunks if
ignored:

- **Two-column layouts** (ResNet, BERT, anything in ACL/CVPR format). Naive
  top-to-bottom extraction interleaves the columns, producing text that reads
  as alternating half-sentences. Columns are detected per page and emitted in
  reading order, with full-width blocks (title, wide figures) splitting the
  page into bands.
- **Running heads and page numbers**, which otherwise land mid-paragraph.
- **The arXiv stamp** rotated up the left margin of page 1, which extracts as a
  line of text belonging to no section.
- **Hyphenated line breaks** ('atten-\\ninformation'), which turn one word into
  two and break exact-match retrieval on the terms that matter most.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf

from papermind.ingest.models import Line

_BOLD_FLAG = 1 << 4

# Fraction of page height treated as running-head / footer territory.
_MARGIN_FRACTION = 0.06
# A repeated margin line must appear on at least this many pages to be dropped,
# so a one-off heading near the top of page 1 survives.
_REPEAT_THRESHOLD = 3

# The dashes here are the real characters PDFs use around page numbers.
_PAGE_NUMBERISH = re.compile(r"^[\s\-–—|]*\d{1,4}[\s\-–—|]*$")  # noqa: RUF001

# Figure/table captions. Their text is worth keeping, but not *here* -- see
# _mark_furniture.
_CAPTION = re.compile(r"^(figure|fig\.|table|algorithm|listing)\s*\d+", re.IGNORECASE)

# A text block within this many points of a figure image, above or below, is
# treated as part of that figure rather than as prose.
_FIGURE_PROXIMITY = 20.0
_DIGITS = re.compile(r"\d+")


def _normalize(text: str) -> str:
    """NFKC-fold and collapse whitespace.

    NFKC turns the ligatures PDF fonts emit ('ﬁ', 'ﬂ') back into ASCII, so
    'classiﬁcation' and 'classification' are the same token to the retriever.
    """
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _block_column(x0: float, x1: float, mid: float, tol: float) -> int:
    """0 = left, 1 = right, -1 = spans the gutter."""
    if x1 <= mid + tol:
        return 0
    if x0 >= mid - tol:
        return 1
    return -1


def _is_two_column(blocks: list[dict[str, Any]], mid: float, tol: float) -> bool:
    """Two-column iff both sides are populated and few blocks cross the gutter.

    Judged on text blocks only. A single-column paper with a centred figure
    would otherwise be misread as two sparse columns.
    """
    sides = [_block_column(b["bbox"][0], b["bbox"][2], mid, tol) for b in blocks]
    if len(sides) < 6:
        return False
    left = sides.count(0)
    right = sides.count(1)
    spanning = sides.count(-1)
    return left >= 3 and right >= 3 and spanning <= 0.25 * len(sides)


def _order_blocks(
    blocks: list[dict[str, Any]], mid: float, tol: float
) -> list[tuple[int, dict[str, Any]]]:
    """Return (column, block) in reading order.

    Single column: plain top-to-bottom. Two columns: walk down the page keeping
    a buffer of column blocks; a full-width block ends the current band, so the
    buffer is flushed (all left, then all right) before it is emitted. That
    handles the common shape -- spanning title, two columns, a wide figure,
    two more columns -- without special-casing any of them.
    """
    blocks = sorted(blocks, key=lambda b: (round(b["bbox"][1], 1), round(b["bbox"][0], 1)))
    if not _is_two_column(blocks, mid, tol):
        return [(0, b) for b in blocks]

    ordered: list[tuple[int, dict[str, Any]]] = []
    band: list[tuple[int, dict[str, Any]]] = []

    def flush() -> None:
        band.sort(key=lambda pair: (pair[0], round(pair[1]["bbox"][1], 1)))
        ordered.extend(band)
        band.clear()

    for block in blocks:
        column = _block_column(block["bbox"][0], block["bbox"][2], mid, tol)
        if column == -1:
            flush()
            ordered.append((0, block))
        else:
            band.append((column, block))
    flush()
    return ordered


def _mark_furniture(
    block: dict[str, Any], images: list[tuple[float, ...]]
) -> bool:
    """Is this text block figure/table furniture rather than prose?

    Two signals, because captions and in-figure labels look different:

    - A block starting 'Figure 2:' / 'Table 3:' is a caption outright.
    - A short, punctuation-free block sitting right above or below a figure
      image is an in-figure label (axis titles, the 'Multi-Head Attention'
      heading inside a diagram).

    This matters because such blocks fall *between* two halves of a sentence in
    reading order. Left in place, the caption of Figure 2 lands in the middle
    of 'The output is computed as a weighted sum ... of the values'. They are
    kept, but moved to the end of their section.

    The label test deliberately also requires the block to be short and not to
    end in sentence punctuation, so a body paragraph that happens to sit close
    under a figure stays prose.
    """
    text = "".join(
        s.get("text", "") for line in block["lines"] for s in line.get("spans", [])
    ).strip()
    if not text:
        return False
    if _CAPTION.match(text):
        return True

    if len(block["lines"]) > 2 or text.endswith((".", "!", "?", ":", ";")):
        return False

    x0, y0, x1, y1 = block["bbox"]
    for ix0, iy0, ix1, iy1 in images:
        horizontally_overlapping = x0 < ix1 and x1 > ix0
        gap = min(abs(y0 - iy1), abs(iy0 - y1))
        if horizontally_overlapping and gap <= _FIGURE_PROXIMITY:
            return True
    return False


def _page_lines(page: pymupdf.Page, page_number: int) -> list[Line]:
    data = page.get_text("dict")
    width = page.rect.width
    mid = width / 2
    tol = width * 0.02

    text_blocks = [b for b in data["blocks"] if b.get("type") == 0 and b.get("lines")]
    images = [tuple(b["bbox"]) for b in data["blocks"] if b.get("type") != 0]
    lines: list[Line] = []

    for column, block in _order_blocks(text_blocks, mid, tol):
        is_furniture = _mark_furniture(block, images)
        for raw_line in block["lines"]:
            # dir is the writing direction; (1, 0) is horizontal. The arXiv
            # stamp up the left margin is (0, 1) and belongs to no section.
            direction = raw_line.get("dir", (1.0, 0.0))
            if abs(direction[1]) > 0.01:
                continue

            spans = raw_line.get("spans", [])
            # Join ALL spans, including whitespace-only ones: in many LaTeX
            # PDFs the inter-word space is its own span, so filtering them
            # first silently glues words together. Only the *typography*
            # measurements below ignore blank spans.
            text = _normalize("".join(s.get("text", "") for s in spans))
            if not text:
                continue

            inked = [s for s in spans if s.get("text", "").strip()]
            if not inked:
                continue

            # Superscript spans are deliberately kept. Dropping them would tidy
            # away footnote markers ('a weighted sum^3 of the values'), but the
            # same flag marks mathematical exponents -- and in an ML paper that
            # means turning the O(n^2 * d) complexity argument that Section 4 is
            # entirely about into O(n * d). A stray footnote digit is much
            # cheaper than corrupted notation.

            lines.append(
                Line(
                    text=text,
                    size=max(float(s["size"]) for s in inked),
                    bold=any(
                        int(s.get("flags", 0)) & _BOLD_FLAG
                        or "bold" in str(s.get("font", "")).lower()
                        for s in inked
                    ),
                    page=page_number,
                    y=float(raw_line["bbox"][1]),
                    x0=float(raw_line["bbox"][0]),
                    column=column,
                    furniture=is_furniture,
                )
            )
    return lines


def _strip_running_heads(pages: list[list[Line]], page_height: float) -> list[Line]:
    """Drop page numbers and repeated running heads from the page margins.

    Repetition is judged with digits masked, so 'Page 3' and 'Page 4' count as
    the same running head.
    """
    top = page_height * _MARGIN_FRACTION
    bottom = page_height * (1 - _MARGIN_FRACTION)

    margin_counts: Counter[str] = Counter()
    for lines in pages:
        seen_on_page = {
            _DIGITS.sub("#", line.text)
            for line in lines
            if line.y < top or line.y > bottom
        }
        margin_counts.update(seen_on_page)

    kept: list[Line] = []
    for lines in pages:
        for line in lines:
            in_margin = line.y < top or line.y > bottom
            if in_margin:
                if _PAGE_NUMBERISH.match(line.text):
                    continue
                if margin_counts[_DIGITS.sub("#", line.text)] >= _REPEAT_THRESHOLD:
                    continue
            kept.append(line)
    return kept


def body_font_size(lines: list[Line]) -> float:
    """The paper's body text size: the char-weighted modal line size.

    Weighted by characters, not line count, because headings and captions are
    numerous but short -- an unweighted mode can land on caption size and make
    every heading test fail.
    """
    histogram: Counter[float] = Counter()
    for line in lines:
        histogram[round(line.size * 2) / 2] += len(line.text)
    if not histogram:
        return 10.0
    return histogram.most_common(1)[0][0]


def extract_lines(pdf_path: Path) -> tuple[list[Line], int]:
    """Extract ordered lines from a PDF. Returns (lines, page_count)."""
    with pymupdf.open(pdf_path) as doc:
        page_count = doc.page_count
        pages = [_page_lines(page, i + 1) for i, page in enumerate(doc)]
        page_height = doc[0].rect.height if page_count else 792.0
    return _strip_running_heads(pages, page_height), page_count


def join_lines(lines: list[Line]) -> str:
    """Reassemble lines into paragraph text, repairing hyphenated line breaks.

    A trailing '-' is only treated as a line-break hyphen when the next line
    starts lowercase; that keeps genuine compounds ('self-attention' split
    across a line break stays hyphenated, 'atten-' + 'tion' is rejoined).
    """
    parts: list[str] = []
    for index, line in enumerate(lines):
        text = line.text
        if text.endswith("-") and index + 1 < len(lines):
            nxt = lines[index + 1].text
            if nxt[:1].islower():
                parts.append(text[:-1])
                continue
        parts.append(text + " ")
    return " ".join("".join(parts).split())
