"""PDF -> ordered, typography-annotated lines (PyMuPDF).

Sectioning needs more than a flat string: it needs to know which lines are set
like headings, which are captions, and where each line sits on the page. This
module produces one ``Line`` per visual row, in reading order, with that
information attached. Everything here is synchronous and CPU-bound; callers in
async code run ``extract_pdf`` via ``asyncio.to_thread``.

The PDF realities handled below each silently corrupt chunks when ignored:

* Two-column layouts. Top-to-bottom extraction interleaves the columns into
  alternating half-sentences. Columns are found per page from the gutter, and
  full-width blocks (title, wide figures and tables) split the page into bands.
* Running heads, footers and page numbers land mid-paragraph; the rotated arXiv
  stamp on page 1 extracts as a line belonging to no section.
* PyMuPDF "lines" are not visual lines. A heading number and its title, the
  cells of a table row, or a subscript that fell into the neighbouring block are
  separate lines on one baseline; they are merged into rows here.
* Sub/superscripts are separate, smaller spans. Joining them naively turns
  ``d_k`` into ``dk`` and ``n^2`` into ``n2``; they are kept and marked instead.
* Figure-internal labels (axis ticks, diagram boxes) are dropped; captions and
  table bodies are kept but flagged so they can move to the end of the section
  rather than splitting the sentence they interrupt on the page.
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Literal

import pymupdf

LineKind = Literal["body", "caption", "table", "figure", "footnote"]
FloatKind = Literal["figure", "table", "algorithm"]


class PDFError(Exception):
    """The PDF cannot be ingested; the message is safe to show to the reader."""


class EncryptedPDF(PDFError):
    pass


class EmptyPDF(PDFError):
    pass


class NoTextPDF(PDFError):
    pass


class NotAPDF(PDFError):
    pass


class PDFTooLarge(PDFError):
    pass


@dataclass(slots=True)
class Line:
    """One visual row of text, in reading order."""

    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    size: float  # dominant font size of the non-script characters
    bold: bool
    font: str
    block: int  # document-unique id of the PyMuPDF block the row started in
    band: int  # page-local vertical band (full-width elements split bands)
    column: int  # 0 = left or single column, 1 = right column
    indent: float = 0.0  # x0 relative to the left edge of this row's column
    fragments: int = 1  # PyMuPDF lines merged into this row
    blocks: frozenset[int] = frozenset()  # every PyMuPDF block the row's fragments came from
    max_gap: float = 0.0  # widest horizontal gap between merged fragments
    smallcaps: bool = False  # small caps were folded back to lower case
    leading_marker: bool = False  # starts with a raised/small marker (footnotes)
    run_in: bool = False  # opens with a bold phrase followed by regular text ('Label Smoothing During ...')
    kind: LineKind = "body"
    float_id: int | None = None  # caption + table body share an id
    # Vertical extent of the ink, for highlight boxes only. PyMuPDF sizes a
    # line's bbox from its fonts' declared ascender/descender, and TeX's math
    # fonts declare enormous ones: a row with a radical or a sum is reported
    # two or three lines tall. Layout keeps using y0/y1 (its thresholds were
    # tuned on them); boxes shown to the reader use this.
    ink_y0: float | None = None
    ink_y1: float | None = None

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def box_y0(self) -> float:
        return self.y0 if self.ink_y0 is None else self.ink_y0

    @property
    def box_y1(self) -> float:
        return self.y1 if self.ink_y1 is None else self.ink_y1

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def region(self) -> tuple[int, int, int]:
        return (self.page, self.band, self.column)


@dataclass(frozen=True, slots=True)
class OutlineEntry:
    """One bookmark from the PDF's own outline (hyperref writes one per heading)."""

    level: int
    title: str
    page: int  # 0-based


@dataclass(slots=True)
class ExtractedDocument:
    lines: list[Line]
    num_pages: int
    page_sizes: list[tuple[float, float]]
    body_size: float
    metadata: dict[str, str] = field(default_factory=dict)
    outline: list[OutlineEntry] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(line.text) for line in self.lines)


# ---------------------------------------------------------------- constants

_BOLD_FLAG = 1 << 4
_SUPERSCRIPT_FLAG = 1
_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi|-medi|cmbx|\bbx\d|\.b$|-b$", re.IGNORECASE)

# Margins (fraction of page height) where running heads and page numbers live.
_TOP_MARGIN = 0.10
_BOTTOM_MARGIN = 0.90
_PAGE_NUMBER = re.compile(
    r"^[\s\-–—|]*(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?[\s\-–—|]*$", re.IGNORECASE
)
_ROMAN_PAGE = re.compile(r"^[ivxlc]{1,6}$", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")

# 'Figure 2:', 'Fig. 3.', 'Table 1:', 'Algorithm 1:'. The delimiter after the
# number is what separates a caption from prose that starts with a reference
# ("Table 2 summarizes our results ...").
_CAPTION = re.compile(
    r"^(?P<kind>figure|fig\.|table|tab\.|algorithm|listing)\s*(?:[A-Z]\.?)?\d{1,3}(?:\.\d{1,2})?\s*[:.|]",
    re.IGNORECASE,
)
_ALGORITHM_CAPTION = re.compile(r"^algorithm\s*\d{1,3}\b", re.IGNORECASE)
# '6 Experiments', '3.2 Attention', 'B.1 Training': a numbered heading row.
_HEADING_LIKE = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,3}|[A-H](?:\.\d{1,2}){0,3})\.?\s+[A-Z]")

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\ufeff]")
# Private-use code points are glyphs from symbol fonts with no Unicode meaning.
_PRIVATE_USE = re.compile(r"[\ue000-\uf8ff]")
# TeX draws an accent as a separate glyph, often a spacing accent ('\u00a8', '\u00b4')
# that NFKC turns into a space plus a combining mark: 'T\u00e4ckstr\u00f6m' extracts as
# 'T \u0308ackstr \u0308om'. Accents above a letter are drawn *before* it; the cedilla and
# ogonek, which hang below, are drawn *after* it ('G \u0308ulc \u0327ehre'). Moving each
# mark onto its letter and recomposing gives the word back. The caron is a
# spacing modifier letter ('\u02c7', U+02C7) that NFKC leaves alone, so it is mapped
# to its combining form first.
_BELOW_ACCENT = re.compile(r"([^\W\d_]) ?([\u0327\u0328])(?: (?=[a-z]))?")
_DETACHED_ACCENT = re.compile(r" ?([\u0300-\u0326\u0329-\u036f]+)([^\W\d_])")
_COMBINING = re.compile(r"[\u0300-\u036f]")
# Only when a letter follows: a hat over a math symbol that extracted on its
# own ('ˆ' then a space) has nothing to attach to.
_SPACING_MODIFIER = re.compile(r"([\u02c6\u02c7\u02d8])(?=[^\W\d_])")
_COMBINING_FOR = {"\u02c7": " \u030c", "\u02c6": " \u0302", "\u02d8": " \u0306"}
_SPACES = re.compile(r"\s+")
# Glyphs that older TeX fonts map to the wrong code point. Computer Modern's
# \epsilon is mapped to 'ǫ' (o with ogonek) in many 2015-era arXiv PDFs; no
# ML paper means the Lithuanian letter.
_GLYPH_FIXES = str.maketrans({"\u01eb": "\u03b5"})
# TeX's math-extension font (CMEX) has no Unicode map in most arXiv PDFs, so
# its glyphs extract as whatever sits at their code points in cmex10: a
# display summation is 'X', a radical 'p', a wide hat 'b' ('bm_t' for the
# Adam paper's m-hat), and big delimiters are control codes that cleaning
# would blank. Only letters and code points that cannot be ordinary text are
# translated: 0x20 is also the space PyMuPDF synthesises between glyphs, and
# ASCII punctuation ('(', '[', '^') is what a CMEX font that does carry a
# Unicode map emits for its own delimiters. Pieces of extensible radicals,
# arrows and braces are dropped.
_CMEX = str.maketrans(
    {
        **dict.fromkeys("PX", "∑"), **dict.fromkeys("QY", "∏"), **dict.fromkeys("RZ", "∫"),
        "S": "⋃", "T": "⋂", "U": "⨄", "V": "⋀", "W": "⋁", "`": "∐", "a": "∐",
        **dict.fromkeys("pqrs", "√"),
        **dict.fromkeys("bcd", "\u0302"), **dict.fromkeys("efg", "\u0303"),
        "h": "[", "i": "]", "j": "⌊", "k": "⌋", "l": "⌈", "m": "⌉", "n": "{", "o": "}",
        **dict.fromkeys("tuvwxyz{|}~", ""),
        **dict.fromkeys("\x00\x10\x12", "("), **dict.fromkeys("\x01\x11\x13!", ")"),
        **dict.fromkeys('\x02\x14"', "["), **dict.fromkeys("\x03\x15#", "]"),
        **dict.fromkeys("\x04\x16$", "⌊"), **dict.fromkeys("\x05\x17%", "⌋"),
        **dict.fromkeys("\x06\x18&", "⌈"), **dict.fromkeys("\x07\x19'", "⌉"),
        **dict.fromkeys("\x08\x1a", "{"), **dict.fromkeys("\x09\x1b", "}"),
        **dict.fromkeys("\x0a\x1c", "⟨"), **dict.fromkeys("\x0b\x1d", "⟩"),
        "\x0c": "|", "\x0d": "‖", **dict.fromkeys("\x0e\x1e", "/"), **dict.fromkeys("\x0f\x1f", "\\"),
    }
)  # fmt: skip
# A wide accent is drawn before the letter it sits on, often with a space in
# front that is a real word break ('estimates bm_t'). _fold_accents would
# delete that space, so the accent rides on a placeholder until it is
# attached after its letter, and becomes a combining mark only after folding.
_ACCENT_PLACEHOLDER = {"\u0302": "\ue0a2", "\u0303": "\ue0a3"}
_PLACEHOLDER_MARKS = str.maketrans({"\ue0a2": "\u0302", "\ue0a3": "\u0303"})

# A span this much smaller than the row's main text is a script (or small caps).
_SCRIPT_SIZE_RATIO = 0.88
# ... and it is raised/lowered when its baseline moves by this fraction of the size.
_SCRIPT_SHIFT_RATIO = 0.12
_FOOTNOTE_MARKER = re.compile(r"^[\d∗*†‡§¶#a-z]{1,3}$")
# A raised number or footnote symbol right after a word is a reference mark
# (or an exponent) of that word, even when TeX left a space before it.
_REFERENCE_MARK = re.compile(r"^[\d∗*†‡§¶]{1,2}$")


# ---------------------------------------------------------------- raw page model


@dataclass(slots=True)
class _Span:
    text: str
    size: float
    bold: bool
    font: str
    x0: float
    x1: float
    baseline: float
    flags: int
    accent: bool = False  # a CMEX wide hat/tilde, to be attached to the next letter


@dataclass(slots=True)
class _Frag:
    """One PyMuPDF line: spans on a shared baseline within one block."""

    spans: list[_Span]
    x0: float
    y0: float
    x1: float
    y1: float
    block: int
    baseline: float
    size: float  # char-weighted main size

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


Rect = tuple[float, float, float, float]


@dataclass(slots=True)
class _Page:
    number: int
    width: float
    height: float
    frags: list[_Frag]
    regions: list[Rect]  # images and vector-drawing clusters
    rules: list[Rect]  # thin horizontal lines (table rules)
    ruled: list[Rect] = field(default_factory=list)  # clusters drawn only with axis-aligned lines


def _clean(text: str) -> str:
    # NFKC folds the ligatures PDF fonts emit ('ﬁ' -> 'fi') and math-alphabet
    # letters back to plain ones, so 'classiﬁcation' and 'classification' are
    # the same token to the retriever.
    text = unicodedata.normalize("NFKC", text).translate(_GLYPH_FIXES)
    text = _CONTROL.sub(" ", text)
    return _PRIVATE_USE.sub("", text)


def _span_text(raw: str, font: str) -> tuple[str, bool]:
    """A span's cleaned text, and whether it is a lone CMEX wide accent."""
    # Non-ASCII text means the font has a Unicode map and is already right.
    if "cmex" not in font.lower() or not raw.isascii():
        return _clean(raw), False
    text = _clean(raw.translate(_CMEX))
    marks = text.strip()
    return text, bool(marks) and all(ch in _ACCENT_PLACEHOLDER for ch in marks)


def _fold_accents(text: str) -> str:
    text = _SPACING_MODIFIER.sub(lambda m: _COMBINING_FOR[m.group(1)], text)
    if not _COMBINING.search(text):
        return text
    folded = _BELOW_ACCENT.sub(lambda m: m.group(1) + m.group(2), text)
    folded = _DETACHED_ACCENT.sub(lambda m: m.group(2) + m.group(1), folded)
    # TeX accents an i by drawing the mark over a dotless i (Lo\"{\i}c).
    folded = re.sub(r"ı(?=[̀-ͯ])", "i", folded)
    return unicodedata.normalize("NFC", folded)


def _is_bold(flags: int, font: str) -> bool:
    return bool(flags & _BOLD_FLAG) or bool(_BOLD_FONT.search(font))


def _main_size(spans: Sequence[_Span]) -> float:
    weights: Counter[float] = Counter()
    for span in spans:
        inked = len(span.text.strip())
        if inked:
            weights[round(span.size, 1)] += inked
    if not weights:
        return max((s.size for s in spans), default=0.0)
    # Largest of the most common sizes: a row that is half subscripts is still
    # set in its main size.
    top = max(weights.values())
    return max(size for size, count in weights.items() if count >= 0.5 * top)


def _read_page(page: pymupdf.Page, number: int, block_offset: int) -> _Page:
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    data = page.get_text("dict", flags=flags)
    frags: list[_Frag] = []
    for block_index, block in enumerate(data.get("blocks", [])):
        if block.get("type", 0) != 0:
            continue
        for raw_line in block.get("lines", []):
            # Writing direction (1, 0) is horizontal. The arXiv stamp up the left
            # margin of page 1 is vertical, as are rotated axis labels in figures;
            # neither belongs to any section.
            direction = raw_line.get("dir", (1.0, 0.0))
            if abs(direction[1]) > 0.05 or direction[0] < 0:
                continue
            spans: list[_Span] = []
            for raw in raw_line.get("spans", []):
                # Whitespace-only spans are kept: many LaTeX PDFs emit the
                # inter-word space as its own span, and dropping it glues words.
                font_name = str(raw.get("font", ""))
                text, accent = _span_text(str(raw.get("text", "")), font_name)
                if not text:
                    continue
                bbox = raw.get("bbox", (0, 0, 0, 0))
                origin = raw.get("origin", (bbox[0], bbox[3]))
                spans.append(
                    _Span(
                        text=text,
                        size=float(raw.get("size", 0.0)),
                        bold=_is_bold(int(raw.get("flags", 0)), str(raw.get("font", ""))),
                        font=str(raw.get("font", "")),
                        x0=float(bbox[0]),
                        x1=float(bbox[2]),
                        baseline=float(origin[1]),
                        flags=int(raw.get("flags", 0)),
                        accent=accent,
                    )
                )
            if not any(s.text.strip() for s in spans):
                continue
            size = _main_size(spans)
            main = [s for s in spans if s.text.strip() and round(s.size, 1) >= round(size, 1) - 0.05]
            baseline = statistics.median(s.baseline for s in main) if main else spans[0].baseline
            x0, y0, x1, y1 = (float(v) for v in raw_line["bbox"])
            frags.append(
                _Frag(
                    spans=spans,
                    x0=x0,
                    y0=y0,
                    x1=x1,
                    y1=y1,
                    block=block_offset + block_index,
                    baseline=baseline,
                    size=size,
                )
            )

    width, height = float(page.rect.width), float(page.rect.height)
    regions: list[Rect] = [_rect(info["bbox"]) for info in page.get_image_info()]
    try:
        drawings = page.get_drawings()
        clusters = page.cluster_drawings(drawings=drawings) if drawings else []
    except Exception:  # pragma: no cover - malformed drawing commands
        drawings, clusters = [], []
    regions.extend(_rect(r) for r in clusters)
    page_area = width * height
    # Rules, underlines and fraction bars are drawings (or 1px images) too; only
    # things with real area are figures. A rectangle covering most of the page is
    # a background or frame, not a figure.
    regions = [
        r
        for r in regions
        if (r[2] - r[0]) >= 20 and (r[3] - r[1]) >= 20 and (r[2] - r[0]) * (r[3] - r[1]) <= 0.8 * page_area
    ]
    return _Page(
        number=number,
        width=width,
        height=height,
        frags=frags,
        regions=regions,
        rules=_horizontal_rules(drawings),
        ruled=[r for r in regions if _only_rules(r, drawings)],
    )


def _only_rules(region: Rect, drawings: Sequence[dict]) -> bool:
    """Is this drawing cluster made of nothing but horizontal/vertical lines and boxes?

    That is a ruled table (or a framed box), not a plot: plots and diagrams
    carry curves, markers and diagonal arrow strokes.
    """
    members = [d for d in drawings if _overlaps(_rect(d["rect"]), _grow(region, 1.0))]
    if not members:
        return False
    for drawing in members:
        for item in drawing.get("items", []):
            op = item[0]
            if op == "l":
                a, b = item[1], item[2]
                if abs(a.x - b.x) > 0.5 and abs(a.y - b.y) > 0.5:
                    return False
            elif op != "re":
                return False
    return True


def _grow(rect: Rect, pad: float) -> Rect:
    return (rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)


def _rect(r: object) -> Rect:
    x0, y0, x1, y1 = (float(v) for v in r)  # type: ignore[attr-defined]
    return (x0, y0, x1, y1)


def _horizontal_rules(drawings: Sequence[dict]) -> list[Rect]:
    """Thin horizontal lines at least 60pt long: booktabs rules, algorithm frames."""
    rules: list[Rect] = []
    for drawing in drawings:
        for item in drawing.get("items", []):
            if item[0] == "l":
                a, b = item[1], item[2]
                if abs(a.y - b.y) < 1.0 and abs(a.x - b.x) >= 60:
                    rules.append((min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)))
            elif item[0] == "re":
                r = item[1]
                if r.height <= 1.5 and r.width >= 60:
                    rules.append(_rect(r))
    return rules


# ---------------------------------------------------------------- document statistics


def _body_size(pages: Sequence[_Page]) -> float:
    """Char-weighted modal font size: the size most of the paper is set in.

    Weighted by characters, not lines, because headings and captions are
    numerous but short. Binned at half a point (justification and font
    rounding produce 9.9/10.0/10.1), then averaged inside the winning bin so a
    9.7pt paper is not treated as 9.5pt.
    """
    bins: dict[float, list[tuple[float, int]]] = defaultdict(list)
    for page in pages:
        for frag in page.frags:
            for span in frag.spans:
                inked = len(span.text.strip())
                if inked and span.size > 0:
                    bins[round(span.size * 2) / 2].append((span.size, inked))
    if not bins:
        return 10.0
    best = max(bins.values(), key=lambda items: sum(n for _, n in items))
    total = sum(n for _, n in best)
    return sum(size * n for size, n in best) / total


def _furniture_key(frag: _Frag) -> str:
    return _SPACES.sub(" ", _DIGITS.sub("#", frag.text.strip().lower()))


def _find_furniture(pages: Sequence[_Page]) -> set[int]:
    """ids of fragments that are running heads, footers, or page numbers.

    Only the top and bottom margins are considered, and a line there is dropped
    when it is a bare page number or recurs (digits masked, so 'Page 3' and
    'Page 4' match) on enough pages. A one-off line in the margin, like the
    conference footnote on page 1, survives.

    A bare number only counts as a page number when nothing else shares its
    row: the '2' of a section heading at the top of a column is also a bare
    number in the top margin, and dropping it loses the section.
    """
    counts: Counter[str] = Counter()
    candidates: list[tuple[_Frag, str, _Page]] = []
    for page in pages:
        seen: set[str] = set()
        for frag in page.frags:
            if frag.y1 <= page.height * _TOP_MARGIN or frag.y0 >= page.height * _BOTTOM_MARGIN:
                key = _furniture_key(frag)
                candidates.append((frag, key, page))
                seen.add(key)
        counts.update(seen)
    threshold = max(2, math.ceil(0.25 * len(pages)))
    dropped: set[int] = set()
    for frag, key, page in candidates:
        text = frag.text.strip()
        bare = bool(_PAGE_NUMBER.match(text) or _ROMAN_PAGE.match(text))
        # Every bare number masks to the same key, so recurrence says nothing
        # about one: it is a page number only if it stands alone.
        if bare:
            if _alone_on_row(frag, page):
                dropped.add(id(frag))
        elif counts[key] >= threshold:
            dropped.add(id(frag))
    return dropped


def _alone_on_row(frag: _Frag, page: _Page) -> bool:
    tolerance = 0.5 * max(frag.size, 1.0)
    return not any(
        other is not frag and abs(other.baseline - frag.baseline) <= tolerance and other.text.strip()
        for other in page.frags
    )


# ---------------------------------------------------------------- layout


def _inside(frag: _Frag, region: tuple[float, float, float, float], pad: float) -> bool:
    x0, y0, x1, y1 = region
    return x0 - pad <= frag.cx <= x1 + pad and y0 - pad <= frag.cy <= y1 + pad


def _figure_zones(regions: Sequence[Rect], join: float = 30.0) -> list[Rect]:
    """Merge images/drawing clusters that sit close together into figure zones.

    A diagram is often several separate drawing clusters (one per column of
    boxes) with its labels in between; per-cluster checks miss those labels.
    """
    zones = [tuple(r) for r in regions]
    merged = True
    while merged:
        merged = False
        for i in range(len(zones)):
            for j in range(i + 1, len(zones)):
                a, b = zones[i], zones[j]
                if (
                    a[0] - join <= b[2]
                    and b[0] - join <= a[2]
                    and a[1] - join <= b[3]
                    and b[1] - join <= a[3]
                ):
                    zones[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del zones[j]
                    merged = True
                    break
            if merged:
                break
    return zones  # type: ignore[return-value]


def _caption_blocks(frags: Sequence[_Frag]) -> dict[int, tuple[FloatKind, Rect]]:
    """Blocks whose first fragment opens a caption, with their kind and extent."""
    by_block: dict[int, list[_Frag]] = defaultdict(list)
    for frag in frags:
        by_block[frag.block].append(frag)
    found: dict[int, tuple[FloatKind, Rect]] = {}
    for block, block_frags in by_block.items():
        first = min(block_frags, key=lambda f: (f.baseline, f.x0))
        kind = caption_kind(_SPACES.sub(" ", first.text).strip())
        if kind is not None:
            rect = (
                min(f.x0 for f in block_frags),
                min(f.y0 for f in block_frags),
                max(f.x1 for f in block_frags),
                max(f.y1 for f in block_frags),
            )
            found[block] = (kind, rect)
    return found


def _adjacent_caption(zone: Rect, rect: Rect, reach: float = 40.0) -> bool:
    """Is a caption block directly above, directly below, or inside this zone?"""
    zx0, zy0, zx1, zy1 = zone
    if rect[0] >= zx1 or rect[2] <= zx0:
        return False
    above = -5 <= zy0 - rect[3] <= reach
    below = -5 <= rect[1] - zy1 <= reach
    inside = zy0 - 2 <= (rect[1] + rect[3]) / 2 <= zy1 + 2
    return above or below or inside


def _overlaps(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _table_zones(
    page: _Page, frags: Sequence[_Frag], captions: dict[int, tuple[FloatKind, Rect]], body_size: float
) -> list[tuple[Rect, int]]:
    """Areas that hold a table or algorithm body, each with the caption block it belongs to.

    Booktabs tables draw no boxes, only horizontal rules with identical
    x-extents; grid tables and framed algorithms are drawing clusters. Either
    becomes a table zone when a table/algorithm caption sits against it -- the
    caption is what makes a set of rules a table rather than, say, the rules
    NeurIPS draws around the paper title.

    Two rules belong to the same table only when no prose sits between them.
    Without that test the rule under an ICLR running head chains through the
    rules of a table further down, and the zone swallows the page.
    """
    top, bottom = page.height * 0.07, page.height * 0.93
    rules = sorted((r for r in page.rules if top < r[1] < bottom), key=lambda r: r[1])
    prose = [f for f in frags if _is_prose_line(f, body_size)]
    candidates: list[Rect] = []
    used = [False] * len(rules)
    for i, first in enumerate(rules):
        if used[i]:
            continue
        group = [first]
        used[i] = True
        for j in range(i + 1, len(rules)):
            rule = rules[j]
            if used[j] or rule[1] - group[-1][3] > 250:
                continue
            overlap = min(first[2], rule[2]) - max(first[0], rule[0])
            if overlap < 0.8 * max(first[2] - first[0], rule[2] - rule[0]):
                continue
            upper = group[-1][3]
            if any(upper < f.cy < rule[1] and f.x0 < rule[2] and f.x1 > rule[0] for f in prose):
                break
            group.append(rule)
            used[j] = True
        zone = (min(r[0] for r in group), group[0][1], max(r[2] for r in group), group[-1][3])
        # Two rules with nothing between them (the gap between a running
        # head's rule and an algorithm's top rule) enclose no table.
        if len(group) >= 2 and any(f.block not in captions and _inside(f, zone, pad=1.0) for f in frags):
            candidates.append(zone)
    candidates.extend(page.regions)
    figures = [rect for kind, rect in captions.values() if kind == "figure"]
    # A drawing cluster holding a figure caption is that figure, even when a
    # table caption happens to sit just above it.
    candidates = [
        zone
        for zone in candidates
        if not any(
            zone[1] <= (rect[1] + rect[3]) / 2 <= zone[3] and _overlaps(zone, rect) for rect in figures
        )
    ]
    tables = {block: rect for block, (kind, rect) in captions.items() if kind != "figure"}
    return _assign_zones(tables, candidates)


def _assign_zones(tables: dict[int, Rect], zones: Sequence[Rect]) -> list[tuple[Rect, int]]:
    """Give each zone to the table caption it belongs to.

    A zone touching one caption is that caption's. A zone touching two -- the
    caption of the table above sits right against the next table's top rule
    when captions go below tables (ACL) -- goes to a caption that has no
    zone yet, so each caption gets its own table. Finally, when one caption
    holds zones on both sides, the nearer side is kept.
    """
    owners = {
        zone: [block for block, rect in tables.items() if _adjacent_caption(zone, rect)] for zone in zones
    }
    assigned: dict[int, list[Rect]] = {block: [] for block in tables}
    for zone, blocks in owners.items():
        if len(blocks) == 1:
            assigned[blocks[0]].append(zone)
    for zone, blocks in owners.items():
        if len(blocks) > 1:
            pool = [block for block in blocks if not assigned[block]] or blocks
            best = min(pool, key=lambda block: _caption_gap(zone, tables[block]))
            assigned[best].append(zone)
    result: list[tuple[Rect, int]] = []
    for block, block_zones in assigned.items():
        result.extend((zone, block) for zone in _caption_side(tables[block], block_zones))
    return result


def _caption_gap(zone: Rect, caption: Rect) -> float:
    return max(zone[1] - caption[3], caption[1] - zone[3], 0.0)


def _caption_side(caption: Rect, zones: Sequence[Rect]) -> list[Rect]:
    """Of the zones assigned to a caption, those inside it plus one side, the nearer.

    A caption sits on one side of its table. When rules or drawings touch it
    from both sides (a table above, a bar chart below), the nearer side is the
    table; the other belongs to the next float.
    """
    inside, above, below = [], [], []
    for zone in zones:
        if zone[3] <= caption[1] + 5:
            above.append(zone)
        elif zone[1] >= caption[3] - 5:
            below.append(zone)
        else:
            inside.append(zone)
    if above and below:
        gap_above = min(caption[1] - z[3] for z in above)
        gap_below = min(z[1] - caption[3] for z in below)
        if gap_above < gap_below:
            below = []
        elif gap_below < gap_above:
            above = []
    return inside + above + below


def _group_table_zones(
    frags: list[_Frag],
    zones: Sequence[tuple[Rect, int]],
    captions: dict[int, tuple[FloatKind, Rect]],
    page_offset: int,
) -> dict[int, tuple[Rect, int]]:
    """Give every fragment inside a table zone one shared block id.

    The layout code reasons about blocks; a table is many tiny blocks (one per
    cell or row), some of which cross the gutter and some of which do not.
    As one block, a full-width table becomes a single full-width band instead
    of being split into two fake columns.
    Returns {synthetic block id: (zone, caption block)}.
    """
    assigned: dict[int, tuple[Rect, int]] = {}
    for index, (zone, caption_block) in enumerate(zones):
        block_id = page_offset + 9_000 + index
        members = [f for f in frags if f.block not in captions and _inside(f, zone, pad=3.0)]
        for frag in members:
            frag.block = block_id
        if members:
            assigned[block_id] = (zone, caption_block)
    return assigned


def _mark_region_labels(
    page: _Page,
    frags: list[_Frag],
    body_size: float,
    captions: dict[int, tuple[FloatKind, Rect]],
    table_zones: Sequence[tuple[Rect, int]],
) -> set[int]:
    """Text inside (or hugging) a figure that reads like a label: short or set small.

    Three guards keep real text: caption blocks are never claimed; zones that
    are tables (see _table_zones) are skipped; and a zone whose text is mostly
    long body-size lines is a framed text box, not a figure.
    """
    labels: set[int] = set()
    figure_captions = [rect for kind, rect in captions.values() if kind == "figure"]
    for zone in _figure_zones(page.regions):
        if any(_overlaps(zone, table) for table, _owner in table_zones):
            continue
        members = [r for r in page.regions if _overlaps(r, zone)]
        if all(r in page.ruled for r in members) and not any(
            _adjacent_caption(zone, rect) for rect in figure_captions
        ):
            # A grid with no figure caption is an inline table ('The following
            # table summarizes the architecture:'); its cells are content.
            continue
        zx0, zy0, zx1, zy1 = zone
        pad = 20.0 if min(zx1 - zx0, zy1 - zy0) >= 100 else 12.0
        inside = [
            f
            for f in frags
            if f.block not in captions
            and _inside(f, zone, pad)
            and not _beyond_caption(f, zone, captions)
            and not (_in_padding(f, zone) and _heading_styled(f, body_size))
        ]
        if not inside:
            continue
        total = sum(len(f.text.strip()) for f in inside)
        prose = sum(len(f.text.strip()) for f in inside if _is_prose_frag(f, body_size))
        if total and prose > 0.5 * total:
            continue
        labels.update(id(f) for f in inside if not _is_prose_frag(f, body_size))
    return labels


def _in_padding(frag: _Frag, zone: Rect) -> bool:
    return not (zone[1] <= frag.cy <= zone[3])


def _heading_styled(frag: _Frag, body_size: float) -> bool:
    """Set like a section heading: larger than body text, or bold and numbered.

    The padding around a figure zone exists to catch axis labels that sit just
    outside the drawing. A heading directly above a figure at the top of a
    page ('Attention Visualizations' over a full-page plot) sits there too, and
    is short, so it looks like a label unless its typography is checked.
    """
    text = _SPACES.sub(" ", frag.text).strip()
    if frag.size >= body_size + 1.0 and len(text.split()) <= 12:
        return True
    bold = sum(len(s.text.strip()) for s in frag.spans if s.bold)
    inked = sum(len(s.text.strip()) for s in frag.spans) or 1
    return bold >= 0.6 * inked and bool(_HEADING_LIKE.match(text))


def _beyond_caption(frag: _Frag, zone: Rect, captions: dict[int, tuple[FloatKind, Rect]]) -> bool:
    """Is this fragment in the zone's padding, on the far side of the figure's own caption?

    Images often carry blank margins that reach past their caption, so the
    padded zone can cover the next heading. A caption ends its figure: what
    sits beyond it (outside the zone proper) is the text that follows.
    """
    zx0, zy0, zx1, zy1 = zone
    if zy0 <= frag.cy <= zy1:
        return False
    for _kind, (cx0, cy0, cx1, cy1) in captions.values():
        if cx1 <= zx0 or cx0 >= zx1:
            continue
        if frag.cy > zy1 and zy0 <= cy1 <= frag.y0 + 1:
            return True
        if frag.cy < zy0 and frag.y1 - 1 <= cy0 <= zy1:
            return True
    return False


def _is_prose_line(frag: _Frag, body_size: float) -> bool:
    """A line of running text, as opposed to a table row PyMuPDF read as one line.

    Rows are mostly digits and have wide gaps between their cells.
    """
    text = frag.text.strip()
    if len(text) < 50 or frag.size < 0.82 * body_size:
        return False
    visible = [ch for ch in text if not ch.isspace()]
    if sum(ch.isalpha() for ch in visible) < 0.75 * len(visible):
        return False
    inked = [s for s in frag.spans if s.text.strip()]
    return all(b.x0 - a.x1 <= 1.5 * frag.size for a, b in pairwise(inked))


def _is_prose_frag(frag: _Frag, body_size: float) -> bool:
    return len(frag.text.strip()) > 40 and frag.size >= 0.82 * body_size


def _text_width(frags: Sequence[_Frag], page_width: float) -> float:
    if not frags:
        return page_width
    xs0 = sorted(f.x0 for f in frags)
    xs1 = sorted(f.x1 for f in frags)
    # Percentiles rather than min/max so one stray margin note cannot widen it.
    lo = xs0[int(0.05 * (len(xs0) - 1))]
    hi = xs1[int(0.95 * (len(xs1) - 1))]
    return max(hi - lo, page_width * 0.3)


def _find_gutter(
    frags: Sequence[_Frag], page_width: float, body_size: float, occupied: Sequence[Rect] = ()
) -> tuple[float, float] | None:
    """The empty vertical strip between two columns, or None for one column.

    Coverage is measured as the total height of body-ish text crossing each x
    in the middle of the page. A two-column page has a run of x where almost
    nothing crosses; a single-column page has none, even with a narrow centred
    figure, because every prose line crosses the centre. Figures and tables
    (``occupied``) count towards a column's share but never as crossing: a page
    whose left column is one tall figure is still a two-column page.
    """
    usable = [f for f in frags if f.size >= 0.75 * body_size and f.width > 2]
    if len(usable) < 8:
        return None
    total = sum(f.y1 - f.y0 for f in usable)
    lo, hi = int(page_width * 0.3), int(page_width * 0.7)
    coverage = []
    for x in range(lo, hi + 1):
        crossing = sum(f.y1 - f.y0 for f in usable if f.x0 < x < f.x1)
        coverage.append((crossing, x))
    best = min(c for c, _ in coverage)
    if best > 0.4 * total:
        return None
    # Widest run of minimal coverage, closest to the centre.
    xs = [x for c, x in coverage if c <= best + 0.01 * total]
    runs: list[tuple[int, int]] = []
    start = prev = xs[0]
    for x in xs[1:]:
        if x != prev + 1:
            runs.append((start, prev))
            start = x
        prev = x
    runs.append((start, prev))
    centre = page_width / 2
    run = min(runs, key=lambda r: abs((r[0] + r[1]) / 2 - centre) - 0.2 * (r[1] - r[0]))
    gl, gr = float(run[0]), float(run[1])
    # Each side must hold lines that fill a column, not just any text: a
    # one-column page of hanging-indent references next to a narrow table
    # leaves a low-coverage strip too, with table cells on its right.
    page_left = min(f.x0 for f in usable)
    page_right = max(f.x1 for f in usable)
    left_fill = 0.5 * (gl - page_left)
    right_fill = 0.5 * (page_right - gr)
    left = sum(f.y1 - f.y0 for f in usable if f.x1 <= gr + 1 and f.width >= left_fill)
    right = sum(f.y1 - f.y0 for f in usable if f.x0 >= gl - 1 and f.width >= right_fill)
    left += sum(r[3] - r[1] for r in occupied if r[2] <= gr + 1)
    right += sum(r[3] - r[1] for r in occupied if r[0] >= gl - 1)
    total += sum(r[3] - r[1] for r in occupied if r[2] <= gr + 1 or r[0] >= gl - 1)
    if left < 0.15 * total or right < 0.15 * total:
        return None
    return gl, gr


def _is_paragraph_block(block_frags: Sequence[_Frag], column_width: float, body_size: float) -> bool:
    wide = [f for f in block_frags if f.width >= 0.8 * column_width and abs(f.size - body_size) <= 1.0]
    return len(wide) >= 3


def _bands(
    frags: list[_Frag],
    gutter: tuple[float, float] | None,
    page_width: float,
    body_size: float,
    *,
    title_page: bool = False,
) -> list[tuple[bool, list[_Frag]]]:
    """Split a page into vertical bands; returns (is_two_column, frags) per band.

    On a two-column page, a block that crosses the gutter (title, abstract in
    some templates, a full-width figure caption or table) ends the current band
    and forms its own. The column text above it is read before it, the column
    text below it after it, which is exactly LaTeX's float behaviour.

    One more cut is needed on title pages: author blocks sit left and right of
    the gutter without crossing it, so without a cut the second author's
    affiliation would be read after the whole left column. A full-width
    horizontal gap ends a band when nothing above it in the band is a paragraph.
    That cut is limited to the first page: elsewhere the top of a column is
    often the last two lines of a paragraph from the previous page, and
    cutting below them reads the rest of the left column under a heading that
    opens the right one.
    """
    if gutter is None:
        return [(False, sorted(frags, key=lambda f: (f.baseline, f.x0)))]
    gl, gr = gutter
    by_block: dict[int, list[_Frag]] = defaultdict(list)
    for frag in frags:
        by_block[frag.block].append(frag)
    blocks = sorted(by_block.values(), key=lambda fs: (min(f.y0 for f in fs), min(f.x0 for f in fs)))
    column_width = max(gl - min((f.x0 for f in frags), default=0.0), 1.0)

    bands: list[tuple[bool, list[_Frag]]] = []
    current: list[_Frag] = []
    current_has_paragraph = False
    spanning: list[_Frag] = []

    def close_current() -> None:
        nonlocal current, current_has_paragraph
        if current:
            bands.append((True, current))
        current = []
        current_has_paragraph = False

    def close_spanning() -> None:
        nonlocal spanning
        if spanning:
            bands.append((False, spanning))
        spanning = []

    for block_frags in blocks:
        bx0 = min(f.x0 for f in block_frags)
        bx1 = max(f.x1 for f in block_frags)
        by0 = min(f.y0 for f in block_frags)
        if bx0 < gl - 1 and bx1 > gr + 1:
            close_current()
            spanning.extend(block_frags)
            continue
        close_spanning()
        if title_page and current and not current_has_paragraph:
            band_bottom = max(f.y1 for f in current)
            if by0 > band_bottom + 4:
                close_current()
        current.extend(block_frags)
        if _is_paragraph_block(block_frags, column_width, body_size):
            current_has_paragraph = True
    close_current()
    close_spanning()
    return bands


def _rows(frags: Iterable[_Frag], isolated: Collection[int] = ()) -> list[list[_Frag]]:
    """Group fragments that share a baseline into visual rows, top to bottom.

    Three guards keep prose safe: baselines must agree to within half the font
    size (adjacent prose lines are ~1.2 sizes apart); merged fragments may not
    overlap horizontally (a tall glyph can make two prose lines' boxes
    overlap, but their text never shares an x range); and a caption or table
    block (``isolated``) is never merged across a gap with text from another
    block. A wrapped figure puts its caption beside the paragraph on the same
    baselines, and merging them interleaves the two line by line
    ('human B (for Figure 5: Layout of ...').
    """
    rows: list[list[_Frag]] = []
    for frag in sorted(frags, key=lambda f: (f.baseline, f.x0)):
        tolerance = 0.5 * max(frag.size, 1.0)
        placed = False
        # Only the last few rows can share a baseline with this fragment.
        for row in reversed(rows[-4:]):
            anchor = row[0]
            if abs(anchor.baseline - frag.baseline) > max(tolerance, 0.5 * anchor.size):
                continue
            if any(_x_overlap(frag, other) > 0.25 * min(frag.width, other.width) for other in row):
                continue
            # Only the horizontally nearest fragment matters: a caption line that
            # PyMuPDF split into two blocks continues from its neighbour, not
            # from the start of the row.
            nearest = min(row, key=lambda other: _x_gap(frag, other))
            if _kept_apart(frag, nearest, isolated):
                continue
            row.append(frag)
            placed = True
            break
        if not placed:
            rows.append([frag])
    for row in rows:
        row.sort(key=lambda f: f.x0)
    rows.sort(key=lambda r: min(f.baseline for f in r))
    return rows


def _x_overlap(a: _Frag, b: _Frag) -> float:
    return max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))


def _x_gap(a: _Frag, b: _Frag) -> float:
    return max(a.x0 - b.x1, b.x0 - a.x1)


def _kept_apart(a: _Frag, b: _Frag, isolated: Collection[int]) -> bool:
    if a.block == b.block or (a.block not in isolated and b.block not in isolated):
        return False
    # Wider than a couple of word spaces: not one line of text that PyMuPDF
    # happened to split into two blocks.
    return _x_gap(a, b) > 0.6 * max(a.size, b.size)


@dataclass(slots=True)
class _RowText:
    text: str
    size: float
    bold: bool
    font: str
    smallcaps: bool
    leading_marker: bool
    run_in: bool


def _render_row(row: Sequence[_Frag], body_size: float) -> _RowText:
    """Join a row's spans into text, with the row's typography.

    Sub/superscripts are kept and marked ('d_k', 'n^2', 'h_{t-1}'). Dropping
    them would tidy footnote markers but corrupt notation -- the O(n^2 * d)
    argument of a complexity section becomes O(n * d) -- and the marked form is
    what readers type ('d_k'), so the lexical retriever can match it.
    """
    spans = [s for frag in row for s in frag.spans]
    inked = [s for s in spans if s.text.strip()]
    size = _main_size(spans)
    letters = "".join(ch for s in inked for ch in s.text if ch.isalpha())
    # Small caps: the whole row is upper case and the 'lower-case' letters are
    # set as smaller capitals on the same baseline. Folding them back recovers
    # 'Adam's Update Rule' from ICLR's 'ADAM'S UPDATE RULE'. The smaller
    # capitals usually outnumber the real ones, so the row's size is the size
    # of the real capitals, not the char-weighted mode.
    # A ratio, not str.isupper(): one math-italic variable in a small-caps
    # heading ('RANK r FOR LORA') must not disable the fold.
    upper = sum(1 for ch in letters if ch.isupper())
    all_caps = len(letters) >= 3 and upper >= 0.85 * len(letters)
    if all_caps and inked:
        big = max(s.size for s in inked)
        big_baseline = statistics.median(s.baseline for s in inked if s.size >= big - 0.05)
        if any(
            s.size < big * 0.92
            and s.size >= big * 0.6
            and abs(s.baseline - big_baseline) <= _SCRIPT_SHIFT_RATIO * big
            and any(ch.isalpha() for ch in s.text)
            for s in inked
        ):
            size = big
    main = [s for s in spans if s.text.strip() and s.size >= size * _SCRIPT_SIZE_RATIO]
    baseline = statistics.median(s.baseline for s in main) if main else spans[0].baseline

    parts: list[str] = []
    smallcaps = False
    leading_marker = False
    run_kind: str | None = None
    run_text: list[str] = []
    prev_x1: float | None = None
    # Horizontal gap between the last inked main-size text and the current
    # script run: a footnote mark sits against its word, while the numerator
    # of an inline fraction ('by 1/sqrt(d_k)') stands a word space or more away.
    ink_x1: float | None = None
    run_x0: float | None = None

    def flush_run() -> None:
        nonlocal run_kind, run_text
        if run_kind is None:
            return
        script = "".join(run_text).strip()
        if script:
            attach = bool(parts) and not parts[-1].endswith(" ")
            before = "".join(parts).rstrip()
            if not attach and run_kind == "^" and before:
                after_punctuation = before.endswith((".", ",", ";", ":", ")"))
                tight = ink_x1 is not None and run_x0 is not None and run_x0 - ink_x1 <= 0.4 * size
                if (after_punctuation and _FOOTNOTE_MARKER.match(script)) or (
                    tight and before[-1].isalpha() and _REFERENCE_MARK.match(script)
                ):
                    # A footnote mark the PDF set after a space ('NLI. 6',
                    # 'gradients 4'): it still belongs to the word it follows,
                    # and marked ('^4') it can be matched to its footnote.
                    parts[:] = [before]
                    attach = True
            if not parts:
                # A mark opening the row: a footnote's number, or a reference
                # mark that wrapped onto a new line. Either way its own token
                # ('4To illustrate' is what joining it would produce).
                marker = script if re.fullmatch(r"[A-Za-z0-9]+", script) else f"{{{script}}}"
                parts.append(f"{run_kind}{marker} ")
            elif not attach:
                parts.append(script)
            elif re.fullmatch(r"[A-Za-z0-9]+", script):
                parts.append(f"{run_kind}{script}")
            else:
                parts.append(f"{run_kind}{{{script}}}")
        run_kind, run_text = None, []

    pending_accent = ""
    for frag_index, frag in enumerate(row):
        if frag_index and prev_x1 is not None:
            gap = frag.x0 - prev_x1
            joined = "".join(parts)
            if gap > 0.15 * size and joined and not joined.endswith(" "):
                flush_run()
                parts.append(" ")
        for span in frag.spans:
            text = span.text
            if span.accent:
                # Keep the space in front (a word break), hold the mark for
                # the letter it is drawn over, which comes next.
                lead = text[: len(text) - len(text.lstrip())]
                if lead:
                    flush_run()
                    parts.append(lead)
                pending_accent += "".join(_ACCENT_PLACEHOLDER[ch] for ch in text.strip())
                continue
            if pending_accent:
                letter = next((i for i, ch in enumerate(text) if ch.isalpha()), None)
                if letter is not None:
                    text = text[: letter + 1] + pending_accent + text[letter + 1 :]
                    pending_accent = ""
            is_small = span.text.strip() != "" and span.size < size * _SCRIPT_SIZE_RATIO
            shift = baseline - span.baseline  # positive = raised (PDF y grows downward)
            if is_small and abs(shift) <= _SCRIPT_SHIFT_RATIO * size:
                # Same baseline, smaller: small caps, or just a smaller font.
                if all_caps and text.strip().isupper():
                    text = text.lower()
                    smallcaps = True
                flush_run()
                parts.append(text)
                continue
            if is_small:
                kind = "^" if shift > 0 else "_"
                if not "".join(parts).strip() and kind == "^":
                    leading_marker = True
                if run_kind != kind:
                    flush_run()
                    run_kind = kind
                    run_x0 = span.x0
                run_text.append(text)
                continue
            flush_run()
            parts.append(text)
            if text.strip():
                ink_x1 = span.x0 + _inked_width(span)
        prev_x1 = frag.x1
    flush_run()

    folded = _fold_accents("".join(parts)).translate(_PLACEHOLDER_MARKS)
    text = _SPACES.sub(" ", unicodedata.normalize("NFC", folded)).strip()
    if not leading_marker and inked:
        first = inked[0]
        # A footnote mark can also be a small digit on the main baseline.
        if first.size < size * _SCRIPT_SIZE_RATIO and _FOOTNOTE_MARKER.match(first.text.strip()):
            leading_marker = True
    bold_chars = sum(len(s.text.strip()) for s in main if s.bold)
    main_chars = sum(len(s.text.strip()) for s in main) or 1
    fonts: Counter[str] = Counter()
    for s in main:
        fonts[s.font] += len(s.text.strip())
    font = fonts.most_common(1)[0][0] if fonts else ""
    return _RowText(
        text=text,
        size=size,
        bold=bold_chars >= 0.6 * main_chars,
        font=font,
        smallcaps=smallcaps,
        leading_marker=leading_marker,
        run_in=_opens_run_in(main),
    )


def _inked_width(span: _Span) -> float:
    """Width of the span without its trailing spaces (PDF span boxes include them)."""
    text = span.text
    if not text or text == text.rstrip():
        return span.x1 - span.x0
    return (span.x1 - span.x0) * len(text.rstrip()) / len(text)


def _opens_run_in(main: Sequence[_Span]) -> bool:
    """Does the row open with a bold phrase and continue in regular weight?

    That is a run-in paragraph heading ('Residual Dropout We apply ...'). One
    bold word could be emphasis inside a paragraph, so the phrase must be two
    or more words or end in punctuation.
    """
    inked = [s for s in main if s.text.strip()]
    lead: list[str] = []
    for index, span in enumerate(inked):
        if not span.bold:
            rest = "".join(s.text for s in inked[index:])
            phrase = " ".join("".join(lead).split())
            if sum(ch.isalpha() for ch in phrase) < 3 or sum(ch.isalpha() for ch in rest) < 3:
                return False
            words = phrase.split()
            return len(words) <= 10 and (len(words) >= 2 or phrase.endswith((".", ":")))
        lead.append(span.text)
    return False


def _layout_page(page: _Page, body_size: float, furniture: set[int]) -> list[Line]:
    frags = [f for f in page.frags if id(f) not in furniture]
    captions = _caption_blocks(frags)
    table_zones = _table_zones(page, frags, captions, body_size)
    zone_blocks = _group_table_zones(frags, table_zones, captions, page_offset=page.number * 10_000)
    labels = _mark_region_labels(page, frags, body_size, captions, table_zones)
    flow = [f for f in frags if id(f) not in labels]

    occupied = [*_figure_zones(page.regions), *(zone for zone, _owner in table_zones)]
    gutter = _find_gutter(flow, page.width, body_size, occupied)
    # Captions and table bodies may sit beside a paragraph on its baselines
    # (wrapped figures and tables); they never share a row with it.
    isolated = set(captions) | set(zone_blocks)
    lines: list[Line] = []
    for band_index, (two_column, band_frags) in enumerate(
        _bands(flow, gutter, page.width, body_size, title_page=page.number == 0)
    ):
        if two_column and gutter is not None:
            split = (gutter[0] + gutter[1]) / 2
            columns = [
                [f for f in band_frags if f.cx < split],
                [f for f in band_frags if f.cx >= split],
            ]
        else:
            columns = [band_frags]
        for column_index, column_frags in enumerate(columns):
            if not column_frags:
                continue
            left_edge = _left_edge(column_frags)
            for row in _rows(column_frags, isolated):
                rendered = _render_row(row, body_size)
                if not rendered.text:
                    continue
                gaps = [b.x0 - a.x1 for a, b in pairwise(row)]
                ink_y0, ink_y1 = _ink_extent(row)
                lines.append(
                    Line(
                        text=rendered.text,
                        page=page.number,
                        x0=min(f.x0 for f in row),
                        y0=min(f.y0 for f in row),
                        x1=max(f.x1 for f in row),
                        y1=max(f.y1 for f in row),
                        size=rendered.size,
                        bold=rendered.bold,
                        font=rendered.font,
                        block=row[0].block,
                        blocks=frozenset(f.block for f in row),
                        band=band_index,
                        column=column_index,
                        indent=min(f.x0 for f in row) - left_edge,
                        fragments=len(row),
                        max_gap=max(gaps, default=0.0),
                        smallcaps=rendered.smallcaps,
                        leading_marker=rendered.leading_marker,
                        run_in=rendered.run_in,
                        ink_y0=ink_y0,
                        ink_y1=ink_y1,
                    )
                )
    _mark_floats(lines, body_size, page.regions, zone_blocks)
    return [line for line in lines if line.kind != "figure"]


# Glyph extent around the baseline, as a fraction of the font size: capitals
# and ascenders reach ~0.7-0.75 above it, descenders ~0.2-0.25 below. A
# little margin on both so the highlight does not clip accents or 'g'.
_INK_ABOVE = 0.85
_INK_BELOW = 0.3


def _ink_extent(row: Sequence[_Frag]) -> tuple[float, float]:
    """The row's vertical ink extent, estimated from span baselines and sizes.

    Never larger than the PyMuPDF bbox: the estimate only trims the inflated
    ascender/descender of math fonts. Scripts count with their own size and
    baseline, so a raised exponent still lies inside.
    """
    top = min(f.y0 for f in row)
    bottom = max(f.y1 for f in row)
    inked = [s for f in row for s in f.spans if s.text.strip()]
    if not inked:
        return top, bottom
    est_top = min(s.baseline - _INK_ABOVE * s.size for s in inked)
    est_bottom = max(s.baseline + _INK_BELOW * s.size for s in inked)
    y0, y1 = max(top, est_top), min(bottom, est_bottom)
    return (y0, y1) if y1 > y0 else (top, bottom)


def _left_edge(frags: Sequence[_Frag]) -> float:
    # The most common left x among the column's lines: indented first lines and
    # centred equations must not define the margin.
    counts: Counter[int] = Counter(round(f.x0) for f in frags if f.width > 20)
    if not counts:
        return min(f.x0 for f in frags)
    edge, _ = counts.most_common(1)[0]
    near = [f.x0 for f in frags if abs(f.x0 - edge) <= 2]
    return min(near) if near else float(edge)


# ---------------------------------------------------------------- floats and footnotes


def caption_kind(text: str) -> FloatKind | None:
    match = _CAPTION.match(text) or _ALGORITHM_CAPTION.match(text)
    if match is None:
        return None
    word = text.split()[0].lower()
    if word.startswith("tab"):
        return "table"
    if word.startswith("alg") or word.startswith("listing"):
        return "algorithm"
    return "figure"


def _paragraph_blocks(lines: Sequence[Line], region_width: float, body_size: float) -> set[int]:
    """Blocks that contain at least one full-width body-size row: prose, never a figure label."""
    return {
        ln.block
        for ln in lines
        if ln.width >= 0.8 * region_width and abs(ln.size - body_size) <= 1.0 and ln.fragments <= 2
    }


def _is_labelish(line: Line, region_width: float, body_size: float, paragraphs: set[int]) -> bool:
    if line.block in paragraphs:
        return False
    return (
        line.width < 0.6 * region_width
        or line.size < 0.85 * body_size
        or line.fragments >= 3
        or (line.fragments >= 2 and line.max_gap > 2 * line.size)
    )


def _mark_floats(
    lines: list[Line], body_size: float, regions: Sequence[Rect], zone_blocks: dict[int, tuple[Rect, int]]
) -> None:
    """Captions, and the table/algorithm bodies and figure labels attached to them.

    A caption anchors a scan through its column: tables and algorithms are read
    downward first (their captions usually sit above them), figures upward.
    Rows are claimed while they stay close together -- the space between a
    float and the surrounding text (LaTeX's \\textfloatsep) is far larger than
    the spacing inside one. Table and algorithm bodies are kept (they are often
    what a question is about); figure-internal labels are not.
    """
    by_region: dict[tuple[int, int, int], list[Line]] = defaultdict(list)
    for line in lines:
        by_region[line.region].append(line)

    float_id = (lines[0].page * 1000) if lines else 0
    for region_lines in by_region.values():
        region_lines.sort(key=lambda ln: ln.y0)
        width = max(ln.x1 for ln in region_lines) - min(ln.x0 for ln in region_lines)
        seen_blocks: set[int] = set()
        for index, line in enumerate(region_lines):
            if line.kind != "body" or line.block in seen_blocks:
                continue
            # Only the first row of a block can open a caption.
            seen_blocks.add(line.block)
            kind = caption_kind(line.text)
            if kind is None:
                continue
            float_id += 1
            caption_rows = _caption_rows(region_lines, index)
            for row in caption_rows:
                row.kind = "caption"
                row.float_id = float_id
            body_kind: LineKind = "figure" if kind == "figure" else "table"
            # The table zones assigned to this caption are claimed whole, gaps
            # and all, wherever the band split put them.
            zone_rows = [
                ln
                for ln in lines
                if ln.kind == "body" and ln.block in zone_blocks and zone_blocks[ln.block][1] == line.block
            ]
            if kind != "figure" and zone_rows:
                for row in zone_rows:
                    row.kind = "table"
                    row.float_id = float_id
                continue
            below = region_lines[index + len(caption_rows) :]
            above = list(reversed(region_lines[:index]))
            scans = [(above, False), (below, True)]
            if kind != "figure":
                scans.reverse()
            caption_span = (min(ln.x0 for ln in caption_rows), max(ln.x1 for ln in caption_rows))
            for candidates, downward in scans:
                start_edge = caption_rows[-1].y1 if downward else line.y0
                edge, span = _skip_regions(start_edge, downward, caption_span, regions, body_size)
                if kind == "figure" and downward and edge == start_edge:
                    # Nothing is drawn below this figure caption, so the figure
                    # is above it and the rows below are the text that follows.
                    continue
                # Paragraph blocks are judged without caption rows: a caption
                # that PyMuPDF merged into the block of the table under it
                # would otherwise make that table look like prose.
                prose_blocks = _paragraph_blocks(
                    [ln for ln in region_lines if ln.kind == "body"], width, body_size
                )
                claimed = _scan_float(candidates, edge, downward, kind, width, body_size, prose_blocks, span)
                claimed_kind = body_kind
                if (
                    kind == "figure"
                    and claimed
                    and edge == start_edge
                    and not _near_graphics(claimed, regions, body_size)
                ):
                    # A "Figure" whose scan crossed no image or drawing and
                    # whose rows sit next to none is set in text: a table the
                    # paper captions as a figure (Batch Normalization's result
                    # tables). Its rows are content, not labels to drop.
                    claimed_kind = "table"
                for row in claimed:
                    row.kind = claimed_kind
                    row.float_id = float_id
                if claimed:
                    break


def _near_graphics(rows: Sequence[Line], regions: Sequence[Rect], body_size: float) -> bool:
    """Do these rows sit on or right next to an image or vector drawing?"""
    x0 = min(row.x0 for row in rows) - body_size
    y0 = min(row.y0 for row in rows) - body_size
    x1 = max(row.x1 for row in rows) + body_size
    y1 = max(row.y1 for row in rows) + body_size
    return any(_overlaps((x0, y0, x1, y1), region) for region in regions)


def _caption_rows(region_lines: Sequence[Line], index: int) -> list[Line]:
    """The rows of the caption that opens at ``region_lines[index]``.

    A caption is one PyMuPDF block -- except when PyMuPDF splits it at a
    subscript, so its last lines come from a second block that started on a
    row of the first. Such a row continues the caption only while the caption
    paragraph is still open (the row before it ran the full width); the same
    second block often goes on into the algorithm or table the caption heads.
    """
    first = region_lines[index]
    own = [ln for ln in region_lines[index:] if ln.block == first.block]
    widest = max(ln.width for ln in own)
    blocks = {first.block}
    rows: list[Line] = []
    open_paragraph = False
    for line in region_lines[index:]:
        continues = open_paragraph and line.kind == "body" and line.block in blocks
        if line.block == first.block or continues:
            rows.append(line)
            blocks.update(line.blocks)
            # A lone sub/superscript that dropped onto a row of its own says
            # nothing about where the paragraph ends.
            if line.width >= 2 * line.size:
                open_paragraph = line.width >= 0.9 * widest
        else:
            open_paragraph = False
    return rows


def _skip_regions(
    edge: float,
    downward: bool,
    caption_span: tuple[float, float],
    regions: Sequence[tuple[float, float, float, float]],
    body_size: float,
) -> tuple[float, tuple[float, float]]:
    """Move the scan edge across images/drawings stacked against the caption.

    Subfigure titles often sit just above an image whose caption is below it;
    the scan has to start from the far side of the image to reach them.
    Returns the new edge and the float's horizontal extent: the caption widened
    by every drawing crossed.
    """
    left, right = caption_span
    moved = True
    while moved:
        moved = False
        for x0, y0, x1, y1 in regions:
            if x1 < caption_span[0] - 5 or x0 > caption_span[1] + 5:
                continue
            if downward and -2 <= y0 - edge <= 2.2 * body_size and y1 > edge:
                edge, moved = y1, True
            elif not downward and -2 <= edge - y1 <= 2.2 * body_size and y0 < edge:
                edge, moved = y0, True
            else:
                continue
            left, right = min(left, x0), max(right, x1)
    return edge, (left, right)


def _scan_float(
    candidates: list[Line],
    anchor_y: float,
    downward: bool,
    kind: FloatKind,
    width: float,
    body_size: float,
    paragraphs: set[int],
    span: tuple[float, float],
) -> list[Line]:
    claimed: list[Line] = []
    edge = anchor_y
    for line in candidates:
        if line.x1 <= span[0] or line.x0 >= span[1]:
            # Beside the float, not part of it: the paragraph a wrapped figure
            # sits next to shares the region and the y range.
            continue
        if line.kind != "body":
            break
        gap = (line.y0 - edge) if downward else (edge - line.y1)
        if gap < -2 and ((downward and line.y1 <= edge) or (not downward and line.y0 >= edge)):
            # Inside an image/drawing the scan jumped over: part of the float.
            if kind != "figure" or _is_labelish(line, width, body_size, paragraphs):
                claimed.append(line)
            continue
        limit = 2.2 * body_size if not claimed else 1.3 * body_size
        if gap > limit:
            break
        if kind == "figure" and not _is_labelish(line, width, body_size, paragraphs):
            break
        if kind != "figure" and line.block in paragraphs and not claimed:
            break
        if kind == "table" and claimed and _prose_row(line, width, body_size):
            # A full-width line of running text after table rows is the
            # paragraph that follows the table, however tight the spacing.
            break
        if caption_kind(line.text) is not None or _heading_like(line, body_size):
            break
        claimed.append(line)
        edge = max(edge, line.y1) if downward else min(edge, line.y0)
    return claimed


def _prose_row(line: Line, region_width: float, body_size: float) -> bool:
    """A row of running text: one fragment, body size, nearly full width, mostly letters."""
    text = line.text.strip()
    visible = [ch for ch in text if not ch.isspace()]
    return (
        line.fragments == 1
        and len(text) >= 40
        and line.width >= 0.75 * region_width
        and abs(line.size - body_size) <= 0.6
        and sum(ch.isalpha() for ch in visible) >= 0.75 * len(visible)
    )


def _heading_like(line: Line, body_size: float) -> bool:
    """Set like a heading: numbered and styled, or bold and clearly larger than body text.

    The second form catches unnumbered headings ('Attention Visualizations')
    that sit right above a figure; without it the figure's upward scan claims
    them as labels.
    """
    styled = line.bold or line.smallcaps or line.size >= body_size + 0.8
    if styled and _HEADING_LIKE.match(line.text):
        return True
    return line.bold and line.size >= body_size + 1.0 and len(line.text.split()) <= 12


# Footnote ids live in their own range so they never collide with float ids.
_FOOTNOTE_ID_BASE = 10_000_000
_LEADING_SCRIPT = re.compile(r"^\^(?:\{([^}]*)\}|(\S+))\s*")


def _mark_footnotes(lines: list[Line], body_size: float, page_heights: Sequence[float]) -> None:
    """Footnotes: a run of small text at the bottom of a column that starts with a marker.

    Without the marker requirement, a references section set in a smaller font
    would be swallowed whole whenever it began in the lower half of a column.

    Runs over the whole document in reading order because a long footnote
    continues at the bottom of the next column (or page) with no marker; its
    tail is recognised only because the previous column's footnote was left
    mid-sentence. Each footnote gets one id shared by its continuation rows,
    so the two halves travel together when footnotes move to the end of
    their section.
    """
    by_column: dict[tuple[int, int], list[Line]] = {}
    for line in lines:
        by_column.setdefault((line.page, line.column), []).append(line)
    next_id = _FOOTNOTE_ID_BASE
    open_note: int | None = None
    for (page, _column), column_lines in by_column.items():
        height = page_heights[page]
        ordered = sorted((ln for ln in column_lines if ln.kind == "body"), key=lambda ln: ln.y0)
        run: list[Line] = []
        for line in reversed(ordered):
            if line.size > body_size - 0.6:
                break
            run.append(line)
        run.reverse()
        first_marker = next((i for i, ln in enumerate(run) if ln.leading_marker), len(run))
        lead, notes = run[:first_marker], run[first_marker:]
        if open_note is None or not lead or lead[0].y0 < 0.5 * height:
            lead = []
        if notes and notes[0].y0 < 0.45 * height:
            notes = []
        for line in lead:
            _set_footnote(line, open_note)
        current = open_note if lead else None
        for line in notes:
            if line.leading_marker or current is None:
                next_id += 1
                current = next_id
            _set_footnote(line, current)
        last = notes[-1] if notes else (lead[-1] if lead else None)
        open_note = current if last is not None and not _sentence_closed(last.text) else None


def _set_footnote(line: Line, note_id: int | None) -> None:
    line.kind = "footnote"
    line.float_id = note_id
    # In body text a raised mark is a reference ('^6'); opening a footnote it
    # is the footnote's own number.
    line.text = _LEADING_SCRIPT.sub(lambda m: (m.group(1) or m.group(2)) + " ", line.text, count=1)


def _sentence_closed(text: str) -> bool:
    return text.rstrip().endswith((".", "!", "?", ")", "”", '"'))


# ---------------------------------------------------------------- entry point


def open_pdf(pdf: bytes | Path) -> pymupdf.Document:
    try:
        if isinstance(pdf, Path):
            return pymupdf.open(pdf)
        return pymupdf.open(stream=pdf, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises a zoo of types for corrupt files
        raise PDFError("This file could not be read as a PDF") from exc


def _read_outline(doc: pymupdf.Document) -> list[OutlineEntry]:
    try:
        toc = doc.get_toc(simple=True)
    except Exception:  # pragma: no cover - a broken outline tree is not worth failing the upload
        return []
    entries: list[OutlineEntry] = []
    for item in toc:
        level, title, page = int(item[0]), _clean(str(item[1])), int(item[2])
        # Page -1 marks a bookmark with no resolvable destination.
        if page >= 1 and title.strip():
            entries.append(OutlineEntry(level=level, title=" ".join(title.split()), page=page - 1))
    return entries


def extract_pdf(pdf: bytes | Path) -> ExtractedDocument:
    """Extract ordered, annotated lines. Blocking: run it in a thread."""
    with open_pdf(pdf) as doc:
        if doc.needs_pass or doc.is_encrypted:
            raise EncryptedPDF("This PDF is password-protected; remove the password and upload it again")
        if doc.page_count == 0:
            raise EmptyPDF("This PDF has no pages")
        pages: list[_Page] = []
        offset = 0
        for number, page in enumerate(doc):
            parsed = _read_page(page, number, offset)
            offset += 10_000
            pages.append(parsed)
        metadata = {k: v for k, v in (doc.metadata or {}).items() if isinstance(v, str) and v.strip()}
        page_sizes = [(p.width, p.height) for p in pages]
        outline = _read_outline(doc)

    body_size = _body_size(pages)
    furniture = _find_furniture(pages)
    lines: list[Line] = []
    for page in pages:
        lines.extend(_layout_page(page, body_size, furniture))
    _mark_footnotes(lines, body_size, [p.height for p in pages])
    document = ExtractedDocument(
        lines=lines,
        num_pages=len(pages),
        page_sizes=page_sizes,
        body_size=body_size,
        metadata=metadata,
        outline=outline,
    )
    if document.char_count < 200:
        raise NoTextPDF("This PDF has no selectable text (it may be a scan); try a PDF with a text layer")
    return document
