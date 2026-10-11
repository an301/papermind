"""Heading detection: ordered lines -> the paper's section structure.

The rule is *pattern AND typography AND sequence*:

* Pattern alone is far too eager. Body text is full of lines that begin with a
  number, and numbered list items, table rows and equation numbers produce more.
* Typography alone is too eager the other way: bold run-in paragraph titles,
  captions and author names are all set like headings.
* Sequence catches what survives both: a bold, numbered-looking "2 GPUs" after
  section 5.4 is not section 2. A numbered heading must plausibly follow the
  previous one (next sibling, first child, or the next top-level section,
  allowing one missed heading).

Typography is judged relative to the paper's own body size, because templates
disagree about everything else: NeurIPS sets subsections in bold at body size,
ICLR in small caps at body size, CVPR in bold at 11pt.

When the PDF carries an outline (hyperref writes one bookmark per numbered
heading), the outline is the authority for numbered headings: each bookmark is
located among the lines on its page and becomes a heading there, and the
typographic detector is limited to what outlines leave out (Abstract,
References, Acknowledgements, and sub-levels deeper than the outline goes).
Bookmark titles are matched on letters and digits only, because hyperref drops
math (a heading's "∆W" is bookmarked as "W") and line breaks can split a
heading anywhere, mid-word hyphen included.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from papermind.ingest.extract import Line, OutlineEntry, caption_kind
from papermind.ingest.ids import disambiguate, section_id
from papermind.schemas.paper import Section

FRONT_MATTER_TITLE = "Front matter"

_NUMBERED = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(?P<title>\S.*)$")
_LETTERED = re.compile(
    r"^(?:appendix\s+)?(?P<num>[A-H](?:\.\d{1,2}){0,3})(?:[.:]|\s*[-–—])?\s+(?P<title>\S.*)$", re.I
)
_BARE_NUMBER = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,3})\.?$")
_RUN_IN_ABSTRACT = re.compile(r"^abstract\s*[.:—–-]\s*(?P<rest>\S.*)$", re.IGNORECASE)

# Headings that are unnumbered in almost every paper. Matched on the whole
# (normalised) title, never as a prefix.
_NAMED = {
    "abstract",
    "introduction",
    "related work",
    "background",
    "conclusion",
    "conclusions",
    "discussion",
    "limitations",
    "broader impact",
    "broader impacts",
    "ethics statement",
    "acknowledgement",
    "acknowledgements",
    "acknowledgment",
    "acknowledgments",
    "references",
    "bibliography",
    "appendix",
    "appendices",
    "supplementary material",
}
_APPENDIX_MARKERS = {"appendix", "appendices", "supplementary material"}
# 'Appendix for "BERT: ..."', 'Supplementary Material for ...'
_APPENDIX_PREFIX = re.compile(r"^(?:appendix|appendices|supplementary material)\b", re.IGNORECASE)
_HEADING_NUMBER = re.compile(
    r"^(?:appendix\s+)?(?P<num>(?:\d{1,2}|[A-Z])(?:\.\d{1,2}){0,3})(?:\.|:|\s*[-–—])?\s+(?P<title>\S.*)$"
)
_ALNUM = re.compile(r"[^0-9a-z]+")
_FOOTNOTE_MARK = re.compile(r"^([\d∗*†‡§¶]{1,2})\s")
# A raised run in extracted text: '^4', '^{∗,†}'.
_SCRIPT_RUN = re.compile(r"\^(?:\{([^}]*)\}|([\d∗*†‡§¶]+))")
_DIGITS = re.compile(r"\d+")

# A bookmark is searched for on its own page, then the next (hyperref anchors
# can land just before a heading pushed to the top of the next page), then the
# previous one.
_OUTLINE_PAGE_OFFSETS = (0, 1, -1)
# Fewer located bookmarks than this means the outline does not describe this
# document's headings (a hand-made outline, a merged PDF); typography decides.
_MIN_OUTLINE_HIT_RATE = 0.6
_BACK_MATTER = {"references", "bibliography"}

_MAX_TITLE_WORDS = 14
_MAX_TITLE_CHARS = 110
_TRAILING = " .:·*†‡§¶"
_LETTER = re.compile(r"[^\W\d_]")
_NUMERIC_TOKEN = re.compile(r"^[\d.,%±×·+\-−/()]+$")


@dataclass
class SectionText:
    """One section of the outline and the lines that belong to it.

    ``lines`` is in reading order for prose, followed by each float that
    belongs to the section (caption first, then its table body) and then the
    section's footnotes -- so a caption never splits a sentence.
    """

    section: Section
    lines: list[Line] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(line.text.strip() for line in self.lines)


@dataclass
class _Heading:
    number: str | None
    title: str
    line: Line
    consumed: int  # rows used by the heading (number on one row, title on the next; wrapped titles)
    rest: Line | None = None  # run-in heading: the text that follows it on the same row
    level: int | None = None  # outline level, for unnumbered bookmarked headings


def _clean_title(title: str) -> str:
    title = " ".join(title.split()).strip(_TRAILING)
    # A trailing footnote mark on a title ('Introduction^1') is not part of it.
    return re.sub(r"\^\{?[\d∗*†‡]+\}?$", "", title).strip(_TRAILING)


def _norm(title: str) -> str:
    return " ".join(_clean_title(title).lower().split())


def _plausible_title(title: str) -> bool:
    words = title.split()
    if not words or len(words) > _MAX_TITLE_WORDS or len(title) > _MAX_TITLE_CHARS:
        return False
    if len(_LETTER.findall(title)) < 2:
        return False
    first_letter = _LETTER.search(title)
    if first_letter is None or not first_letter.group().isupper():
        return False
    if title.endswith((",", ";", "-", "–")) or any(ch in title for ch in "=<>|∑"):
        return False
    numeric = sum(1 for word in words if _NUMERIC_TOKEN.match(word))
    # Table rows parse as headings ('3.3 28.4 41.8'); no real title is mostly numbers.
    return numeric <= 0.4 * len(words)


def _styled(line: Line, body_size: float) -> bool:
    return line.bold or line.smallcaps or line.size >= body_size + 0.8


def _number_tuple(number: str) -> tuple[int, ...]:
    parts = number.split(".")
    head = parts[0]
    first = ord(head.upper()) - ord("A") + 1 if head.isalpha() else int(head)
    return (first, *(int(p) for p in parts[1:]))


def _follows(prev: tuple[int, ...] | None, cand: tuple[int, ...]) -> bool:
    """Is ``cand`` a plausible next section number after ``prev``?

    Allows the next sibling at any level, a first child, or the next top-level
    section -- each with one skipped number, so one missed heading does not
    cascade into rejecting the rest of the paper.
    """
    if prev is None:
        return cand[0] <= 3 and all(n <= 2 for n in cand[1:])
    depth = len(cand)
    if depth <= len(prev) and cand[:-1] == prev[: depth - 1] and 1 <= cand[-1] - prev[depth - 1] <= 2:
        return True
    if depth == len(prev) + 1 and cand[:-1] == prev and cand[-1] in (1, 2):
        return True
    if depth == len(prev) + 2 and cand[:-2] == prev and cand[-2:] == (1, 1):
        return True
    return 1 <= cand[0] - prev[0] <= 2 and all(n <= 1 for n in cand[1:])


class _Detector:
    def __init__(self, body_size: float, outline_depth: int | None = None) -> None:
        self.body_size = body_size
        # Numbered headings at or above this depth come from the PDF outline only.
        self.outline_depth = outline_depth
        self.prev_numbered: tuple[int, ...] | None = None
        self.prev_lettered: tuple[int, ...] | None = None
        self.in_back_matter = False  # after References or an Appendix marker
        self.seen_heading = False

    def match(self, lines: list[Line], index: int) -> _Heading | None:
        line = lines[index]
        if line.kind != "body" or len(line.text) > _MAX_TITLE_CHARS + 20:
            return None
        text = line.text.strip()
        if caption_kind(text) is not None:
            return None
        styled = _styled(line, self.body_size)

        # Run-in abstract ('Abstract—We propose ...', 'Abstract. We ...').
        run_in = _RUN_IN_ABSTRACT.match(text)
        if run_in and not self.seen_heading and (styled or line.fragments > 1 or text.startswith("Abstract")):
            rest = replace(line, text=run_in.group("rest"))
            return _Heading(None, "Abstract", line, 1, rest=rest)

        named = _norm(text)
        if named in _NAMED:
            # 'Abstract' and 'References' are reliably headings even when a
            # template sets them at body weight, as long as they stand alone.
            if styled or line.fragments == 1:
                return _Heading(None, _clean_title(text), line, 1)
            return None

        if not styled:
            return None

        if _APPENDIX_PREFIX.match(text):
            title = _clean_title(text)
            if _plausible_title(title) or len(title.split()) <= 4:
                return _Heading(None, title, line, 1)

        bare = _BARE_NUMBER.match(text)
        if bare and index + 1 < len(lines):
            nxt = lines[index + 1]
            title = _clean_title(nxt.text)
            if nxt.kind == "body" and _styled(nxt, self.body_size) and _plausible_title(title):
                if self._accept_number(bare.group("num"), line):
                    return _Heading(bare.group("num"), title, line, 2)
            return None

        numbered = _NUMBERED.match(text)
        if numbered:
            title = _clean_title(numbered.group("title"))
            if _plausible_title(title) and self._accept_number(numbered.group("num"), line):
                return _Heading(numbered.group("num"), title, line, 1)
            return None

        lettered = _LETTERED.match(text)
        if lettered:
            title = _clean_title(lettered.group("title"))
            number = lettered.group("num").upper()
            if _plausible_title(title) and self._accept_letter(number, line):
                return _Heading(number, title, line, 1)
            return None

        # Unnumbered, un-named heading: only on strong evidence (larger than body
        # AND bold/caps), and never before the first real heading -- that would
        # make the paper title a section.
        title = _clean_title(text)
        if (
            self.seen_heading
            and line.size >= self.body_size + 1.0
            and (line.bold or line.smallcaps or title.isupper())
            and _plausible_title(title)
            and len(title.split()) <= 10
        ):
            return _Heading(None, title, line, 1)
        return None

    def _outline_owns(self, number: str) -> bool:
        return self.outline_depth is not None and number.count(".") + 1 <= self.outline_depth

    def _accept_number(self, number: str, line: Line) -> bool:
        if self._outline_owns(number):
            return False
        cand = _number_tuple(number)
        if self.in_back_matter and self.prev_lettered is not None:
            return False
        if _follows(self.prev_numbered, cand):
            return True
        # Clearly set as a heading (well above body size and bold): trust the
        # typography over the sequence; some papers renumber or skip.
        return (
            line.size >= self.body_size + 1.5 and line.bold and self.prev_numbered is not None and cand[0] > 0
        )

    def _accept_letter(self, number: str, line: Line) -> bool:
        if self._outline_owns(number):
            return False
        cand = _number_tuple(number)
        clearly_heading = line.size >= self.body_size + 0.8 or self.in_back_matter
        if not clearly_heading:
            return False
        # Lettered sections are appendices: they follow the numbered body or
        # the references. Before either, a large bold 'A ...' is the paper's
        # title ('A Simple Framework for ...'), not appendix A.
        if self.prev_numbered is None and not self.in_back_matter:
            return False
        if self.prev_lettered is None:
            return cand[0] == 1 and all(n <= 1 for n in cand[1:])
        return _follows(self.prev_lettered, cand)

    def commit(self, heading: _Heading) -> None:
        self.seen_heading = True
        number = heading.number
        named = _norm(heading.title)
        if number is None:
            if named in _APPENDIX_MARKERS or named in _BACK_MATTER or _APPENDIX_PREFIX.match(named):
                self.in_back_matter = True
            return
        if number[0].isalpha():
            self.prev_lettered = _number_tuple(number)
            self.in_back_matter = True
        else:
            self.prev_numbered = _number_tuple(number)


def _continuation(lines: list[Line], start: int, heading: _Heading, detector: _Detector) -> int:
    """Extra rows of a heading title that wrapped onto the next line."""
    # A run-in heading ('Abstract—We propose ...') and a named one ('References')
    # are complete as matched; the row under them is body text set in the
    # same style, which would otherwise read as a wrapped title.
    if heading.rest is not None or _norm(heading.title) in _NAMED:
        return 0
    extra = 0
    last = lines[start + heading.consumed - 1]
    while True:
        index = start + heading.consumed + extra
        if index >= len(lines) or extra >= 2:
            return extra
        nxt = lines[index]
        # Not required to share a PyMuPDF block: centred title rows are often
        # one block each. Identical style at a one-row pitch is the stronger signal.
        same_style = (
            nxt.kind == "body"
            and abs(nxt.size - last.size) <= 0.3
            and nxt.bold == last.bold
            and nxt.smallcaps == last.smallcaps
            and _next_row(last, nxt)
        )
        if not same_style:
            return extra
        # A wrapped title's second row is set exactly like its first, so an
        # unnumbered 'strong evidence' match on it is the same heading going on;
        # only a numbered or named heading starts a new one.
        following = detector.match(lines, index)
        if following is not None and (following.number is not None or _norm(following.title) in _NAMED):
            return extra
        joined = _join_title(heading.title, nxt.text)
        if not _plausible_title(joined):
            return extra
        heading.title = _clean_title(joined)
        last = nxt
        extra += 1


def _next_row(last: Line, nxt: Line) -> bool:
    """Is ``nxt`` the row directly under ``last`` (a wrapped heading's next row)?

    Measured top to top: line boxes of large type overlap their neighbours
    (a 14pt heading's box is ~20pt tall), so bottom-to-top gaps go negative.
    """
    pitch = nxt.y0 - last.y0
    return nxt.page == last.page and nxt.column == last.column and 0.5 * last.size < pitch <= 1.8 * last.size


def _join_title(left: str, right: str, reference: str | None = None) -> str:
    """Join two rows of a wrapped heading, repairing a line-end hyphen.

    'Batch-' + 'Normalized' keeps its hyphen when the reference (the outline's
    copy of the title) has it, or when there is no reference and both halves
    are capitalised (title-case compounds); otherwise the hyphen was TeX's.
    """
    left, right = left.rstrip(), right.strip()
    if not left.endswith("-") or len(left) < 2 or not left[-2].isalpha() or not right[:1].isalpha():
        return f"{left} {right}"
    stem = left[:-1]
    head = stem.split()[-1] if stem.split() else stem
    tail = right.split()[0]
    if reference is not None:
        keep = f"{head}-{tail}".lower() in reference.lower()
    else:
        keep = right[:1].isupper()
    return f"{stem}-{right}" if keep else f"{stem}{right}"


def _alnum(text: str) -> str:
    # Sub/superscript markers are part of the extracted text ('d_k'); dropping
    # every non-alphanumeric makes the line comparable with the bookmark ('dk').
    return _ALNUM.sub("", text.lower())


def _split_number(text: str) -> tuple[str | None, str]:
    match = _HEADING_NUMBER.match(text.strip())
    if match is None:
        return None, text.strip()
    return match.group("num"), match.group("title")


def _same_key(found: str, key: str) -> bool:
    # A trailing footnote mark on the heading ('Introduction^1') is not in the bookmark.
    return found == key or (
        found.startswith(key) and found[len(key) :].isdigit() and len(found) - len(key) <= 2
    )


def _match_entry(
    lines: list[Line], index: int, entry: OutlineEntry, key: str, body_size: float
) -> _Heading | None:
    """Does a heading for this bookmark start at ``lines[index]``?"""
    first = lines[index]
    if first.kind != "body" or len(first.text) > _MAX_TITLE_CHARS + 40:
        return None
    number: str | None = None
    rows = [first]
    bare = _BARE_NUMBER.match(first.text.strip())
    if bare:
        # The number set on a row of its own, the title on the next.
        if index + 1 >= len(lines) or lines[index + 1].page != first.page:
            return None
        number = bare.group("num")
        rows = [lines[index + 1]]
        if rows[0].kind != "body":
            return None
    if not (_styled(rows[0], body_size) or number is not None or _split_number(rows[0].text)[0] is not None):
        return None

    whole = _alnum(rows[0].text)
    line_number, title_part = _split_number(rows[0].text)
    stripped = _alnum(title_part)
    while True:
        for candidate, num in ((stripped, line_number), (whole, None)):
            if _same_key(candidate, key):
                consumed = len(rows) + (1 if bare else 0)
                title_text = title_part if num is not None else rows[0].text
                for row in rows[1:]:
                    title_text = _join_title(title_text, row.text, entry.title)
                return _Heading(
                    number=number or num,
                    title=_clean_title(title_text),
                    line=first,
                    consumed=consumed,
                    level=entry.level,
                )
        nxt_index = index + len(rows) + (1 if bare else 0)
        if (
            len(rows) >= 3
            or nxt_index >= len(lines)
            or not (key.startswith(stripped) or key.startswith(whole))
        ):
            return None
        last, nxt = rows[-1], lines[nxt_index]
        if nxt.kind != "body" or not _next_row(last, nxt):
            return None
        rows.append(nxt)
        whole += _alnum(nxt.text)
        stripped += _alnum(nxt.text)


def locate_outline(
    lines: list[Line], outline: Sequence[OutlineEntry], body_size: float
) -> dict[int, _Heading]:
    """Place the PDF's bookmarks on lines: {first line index: heading}.

    Bookmarks are in document order, so the search only moves forward; one
    that cannot be found is skipped rather than allowed to drag the cursor.
    Returns {} when too few are found for the outline to be trusted.
    """
    if len(outline) < 3:
        return {}
    pages: dict[int, list[int]] = {}
    for position, line in enumerate(lines):
        pages.setdefault(line.page, []).append(position)
    found: dict[int, _Heading] = {}
    cursor = 0
    for entry in outline:
        outline_number, outline_title = _split_number(entry.title)
        key = _alnum(outline_title)
        if len(key) < 2:
            continue
        hit: tuple[int, _Heading] | None = None
        for offset in _OUTLINE_PAGE_OFFSETS:
            for position in pages.get(entry.page + offset, []):
                if position < cursor:
                    continue
                heading = _match_entry(lines, position, entry, key, body_size)
                if heading is not None:
                    hit = (position, heading)
                    break
            if hit is not None:
                break
        if hit is None:
            continue
        position, heading = hit
        if heading.number is None and outline_number is not None:
            heading.number = outline_number
        found[position] = heading
        cursor = position + heading.consumed
    if len(found) < _MIN_OUTLINE_HIT_RATE * len(outline):
        return {}
    return found


def detect_sections(
    lines: list[Line], body_size: float, outline: Sequence[OutlineEntry] = ()
) -> list[SectionText]:
    """Split ordered lines into sections. Returns the full outline, empty sections included."""
    anchors = locate_outline(lines, outline, body_size)
    outline_depth = max((entry.level for entry in outline), default=0) if anchors else None
    detector = _Detector(body_size, outline_depth=outline_depth)
    sections: list[SectionText] = []
    current = SectionText(Section(section_id="", number=None, title=FRONT_MATTER_TITLE, page=0, y=0.0))
    index = 0
    while index < len(lines):
        heading = anchors.get(index)
        extra = 0
        if heading is None:
            heading = detector.match(lines, index)
            if heading is None:
                current.lines.append(lines[index])
                index += 1
                continue
            extra = _continuation(lines, index, heading, detector)
        detector.commit(heading)
        sections.append(current)
        if heading.number is not None:
            level = heading.number.count(".") + 1
        else:
            level = heading.level or 1
        current = SectionText(
            Section(
                section_id="",
                number=heading.number,
                title=heading.title,
                level=level,
                page=heading.line.page,
                y=round(heading.line.y0, 1),
            )
        )
        if heading.rest is not None:
            current.lines.append(heading.rest)
        index += heading.consumed + extra
    sections.append(current)

    # The front matter (title block, authors) only exists if it has text.
    if sections and sections[0].section.title == FRONT_MATTER_TITLE and sections[0].is_empty:
        sections.pop(0)
    if sections and sections[0].section.title == FRONT_MATTER_TITLE:
        first_line = sections[0].lines[0]
        sections[0].section.page = first_line.page
        sections[0].section.y = round(first_line.y0, 1)

    _relocate_floats(sections)
    ids = disambiguate([section_id(s.section.number, s.section.title) for s in sections])
    for text, sid in zip(sections, ids, strict=True):
        text.section.section_id = sid
    return sections


def _footnote_mark(line: Line) -> str | None:
    """The number or symbol a footnote opens with ('4 To illustrate ...' -> '4')."""
    if not line.leading_marker:
        return None
    match = _FOOTNOTE_MARK.match(line.text)
    return match.group(1) if match else None


def _cites(text: str, mark: str) -> bool:
    """Does body text carry the raised reference mark ('^4', '^{14}', '^{∗†}')?"""
    for braced, bare in _SCRIPT_RUN.findall(text):
        script = braced or bare
        # Numbers are whole tokens ('^{14}' does not cite footnote 1); symbols
        # stack without separators ('Gomez^{∗†}' cites both).
        if (mark in _DIGITS.findall(script)) if mark.isdigit() else (mark in script):
            return True
    return False


def _footnote_homes(sections: list[SectionText]) -> dict[int, int]:
    """Each footnote's section: the one whose text cites it.

    A footnote is set at the bottom of the column (or page) that cites it, so
    in reading order it lands under whatever heading came last on that page:
    footnote 4 of the Transformer paper, the variance argument for scaling by
    sqrt(d_k), would otherwise end up in the next subsection. The citing line is
    the nearest earlier body line on the same page with the footnote's mark. A
    mark the footnote's own section carries keeps it there; no match keeps it
    there too (an exponent '^2' can look like a mark, so the walk never leaves
    the page).
    """
    flat = [(position, line) for position, text in enumerate(sections) for line in text.lines]
    homes: dict[int, int] = {}
    for index, (position, line) in enumerate(flat):
        if line.kind != "footnote" or line.float_id is None or line.float_id in homes:
            continue
        homes[line.float_id] = position
        mark = _footnote_mark(line)
        if mark is None:
            continue
        for earlier in range(index - 1, -1, -1):
            cited_in, candidate = flat[earlier]
            if candidate.page != line.page:
                break
            if candidate.kind == "body" and _cites(candidate.text, mark):
                homes[line.float_id] = cited_in
                break
    return homes


def _relocate_floats(sections: list[SectionText]) -> None:
    """Move captions and table bodies to the end of their caption's section, footnotes after them.

    A float belongs to the section its caption appears in; its table body may
    sit on the other side of a heading in reading order (caption below the
    table, heading between), so rows are regrouped by float id first. A
    footnote belongs to the section that cites it (see ``_footnote_homes``);
    its continuation at the bottom of the next column follows it.
    """
    home: dict[int, int] = _footnote_homes(sections)
    for position, text in enumerate(sections):
        for line in text.lines:
            if line.kind == "caption" and line.float_id is not None:
                home.setdefault(line.float_id, position)
    moved: dict[int, list[Line]] = {}
    for position, text in enumerate(sections):
        for line in text.lines:
            if line.kind in ("caption", "table", "footnote") and line.float_id is not None:
                moved.setdefault(home.get(line.float_id, position), []).append(line)
            elif line.kind == "footnote":
                moved.setdefault(position, []).append(line)

    for position, text in enumerate(sections):
        body = [ln for ln in text.lines if ln.kind not in ("caption", "table", "footnote")]
        members = moved.get(position, [])
        order: list[int | None] = []
        for line in members:
            if line.float_id not in order:
                order.append(line.float_id)
        floats: list[Line] = []
        notes: list[Line] = []
        for fid in order:
            group = [ln for ln in members if ln.float_id == fid]
            floats.extend(ln for ln in group if ln.kind == "caption")
            floats.extend(ln for ln in group if ln.kind == "table")
            notes.extend(ln for ln in group if ln.kind == "footnote")
        text.lines = body + floats + notes
