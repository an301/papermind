"""Heading detection: ordered lines -> the paper's actual section structure.

Chunking on section headers (rather than a fixed window) is what makes the IDs
human-readable and the chunks map onto how the paper is organised -- but it
only works if headings are found reliably. The rule used here is
**pattern AND typography**: a line must both look like a heading and be set
like one.

Pattern alone is far too eager. Body text is full of lines that begin with a
number ('3.2 shows that...', '1. First we consider...'), and reference lists
and numbered equations produce more. Requiring a typographic signal -- bold, or
larger than body text -- removes essentially all of them, because a paper that
sets its headings the same as its body text is not one a human can skim either.

Typography alone is too eager in the other direction: figure captions and
author affiliations are frequently bold.
"""

from __future__ import annotations

import re

from papermind.ingest.extract import body_font_size
from papermind.ingest.models import Line, Section

# '3', '3.2', '3.2.1' followed by a title. The trailing dot is optional because
# both '3.2 Attention' and '3.2. Attention' occur in the wild.
_NUMBERED = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+(?P<title>\S.*)$")

# Appendix headings: 'A Proofs', 'B.1 Extra results'.
_LETTERED = re.compile(r"^(?P<num>[A-Z](?:\.\d{1,2}){0,2})\.?\s+(?P<title>\S.*)$")

# A line that is nothing but a section number; the title is on the next line.
_BARE_NUMBER = re.compile(r"^(?P<num>\d{1,2}(?:\.\d{1,2}){0,3})\.?$")

# Headings that carry no number in almost every paper.
_UNNUMBERED = {
    "abstract",
    "acknowledgement",
    "acknowledgements",
    "acknowledgment",
    "acknowledgments",
    "appendix",
    "background",
    "conclusion",
    "conclusions",
    "discussion",
    "introduction",
    "limitations",
    "references",
    "related work",
}

_MAX_HEADING_CHARS = 90

# A heading must exceed body size by this much (points) to count on size alone.
_SIZE_MARGIN = 0.4

# Lines that look like headings but never are.
_CAPTION_PREFIX = re.compile(r"^(figure|fig\.|table|algorithm|equation|listing)\b", re.IGNORECASE)

_LETTER = re.compile(r"[A-Za-z]")
# A token made only of digits and numeric punctuation: '28.4', '1018', '1.2', '±0.3'.
# The unicode operators are intentional: results tables are full of them.
_NUMERIC_TOKEN = re.compile(r"^[\d.,%±×·+\-/]+$")  # noqa: RUF001
# Above this share of numeric tokens, a line is table data, not a title.
_MAX_NUMERIC_TOKEN_RATIO = 0.4


def _is_plausible_title(title: str) -> bool:
    """Reject table rows that pattern-match as headings.

    Results tables are the one place where bold, numbered-looking lines appear
    in quantity: a row like '3.3 · 1018' parses as section 3.3 titled '1018',
    and the BLEU pair '28.4' / '41.8' parses as a numbered heading with a
    numeric title. Both are bold and both sit at body size, so typography
    cannot separate them -- but no real section title is mostly digits.
    """
    if len(_LETTER.findall(title)) < 2:
        return False
    tokens = title.split()
    if not tokens:
        return False
    numeric = sum(1 for token in tokens if _NUMERIC_TOKEN.match(token))
    return numeric <= _MAX_NUMERIC_TOKEN_RATIO * len(tokens)


def _has_heading_typography(line: Line, body_size: float) -> bool:
    return line.bold or line.size >= body_size + _SIZE_MARGIN


def _clean_title(title: str) -> str:
    return title.strip(" .·*†‡§¶").strip()


def _level(number: str | None) -> int:
    return 1 if not number else number.count(".") + 1


def _match_heading(line: Line, body_size: float) -> tuple[str | None, str] | None:
    """Return (number, title) if this line is a heading, else None."""
    text = line.text
    if len(text) > _MAX_HEADING_CHARS or _CAPTION_PREFIX.match(text):
        return None

    lowered = _clean_title(text).lower()
    if lowered in _UNNUMBERED:
        # 'Abstract' and 'References' are reliably set as headings; accept them
        # on the keyword alone so a paper that sets them at body weight still
        # segments correctly.
        return None, _clean_title(text)

    if not _has_heading_typography(line, body_size):
        return None

    numbered = _NUMBERED.match(text)
    if numbered:
        title = _clean_title(numbered.group("title"))
        if _is_plausible_title(title):
            return numbered.group("num"), title
        return None

    lettered = _LETTERED.match(text)
    if lettered:
        title = _clean_title(lettered.group("title"))
        # Appendix headings are title-case ('A Proofs'); this rejects bold body
        # sentences that happen to start with a lone capital ('A simple ...').
        if title[:1].isupper() and _is_plausible_title(title):
            return lettered.group("num"), title
        return None

    return None


def split_into_sections(lines: list[Line]) -> list[Section]:
    """Segment the document at headings.

    Everything before the first heading (title block, author list) becomes a
    `frontmatter` section rather than being dropped -- ingestion should be
    lossless, and filtering is cheap downstream.
    """
    if not lines:
        return []

    body_size = body_font_size(lines)
    sections: list[Section] = []
    current = Section(number=None, title="frontmatter", page_start=lines[0].page)

    index = 0
    while index < len(lines):
        line = lines[index]

        # Number on one line, title on the next -- treat as a single heading.
        bare = _BARE_NUMBER.match(line.text)
        if (
            bare
            and _has_heading_typography(line, body_size)
            and index + 1 < len(lines)
            and len(lines[index + 1].text) <= _MAX_HEADING_CHARS
            and _has_heading_typography(lines[index + 1], body_size)
            and _is_plausible_title(_clean_title(lines[index + 1].text))
        ):
            number = bare.group("num")
            title = _clean_title(lines[index + 1].text)
            if current.lines or current.title != "frontmatter":
                sections.append(current)
            current = Section(number, title, line.page, _level(number))
            index += 2
            continue

        heading = _match_heading(line, body_size)
        if heading is not None:
            number, title = heading
            if current.lines or current.title != "frontmatter":
                sections.append(current)
            current = Section(number, title, line.page, _level(number))
            index += 1
            continue

        current.add(line)
        index += 1

    if current.lines:
        sections.append(current)

    # A parent heading with no prose of its own -- '6 Results' immediately
    # followed by '6.1 Machine Translation' -- yields an empty section. Drop it:
    # there is nothing to retrieve, and an empty chunk would be a dead eval
    # label. The structure stays legible in its children's section numbers.
    return [s for s in sections if s.lines]
