"""Sections -> chunks.

The splitting strategy is deliberately layered, cheapest structure first:

1. **A section that fits is one chunk.** This is the common case and the reason
   the IDs read like a table of contents.
2. **A section that does not fit is packed by paragraph.** Paragraphs are
   greedily accumulated up to the budget, so splits land on boundaries a reader
   would recognise and no sentence is cut.
3. **A single paragraph larger than the budget falls back to character
   splitting**, preferring the last sentence boundary in the window so even the
   worst case usually breaks at a full stop. This is the backstop for the two
   things that defeat paragraph logic: reference lists (one unbroken run of
   entries) and tables flattened into prose.

Paragraph boundaries come from line geometry, not from blank lines -- extracted
PDF text has no blank lines. A gap noticeably larger than the section's own
median line spacing, or a first-line indent, starts a new paragraph.
"""

from __future__ import annotations

import re
from itertools import pairwise

from papermind.ingest.extract import join_lines
from papermind.ingest.ids import chunk_id, disambiguate, section_key
from papermind.ingest.models import Chunk, Line, Section
from papermind.ingest.tokens import estimate_tokens, estimate_tokens_from_chars

# A vertical gap this much larger than the median starts a new paragraph.
_PARAGRAPH_GAP_RATIO = 1.35
# An indent this many points beyond the section's left margin does the same.
_INDENT_POINTS = 6.0

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# Only used by the character-based backstop, to convert a token budget into a
# character window. Matches the estimator in tokens.py.
_CHARS_PER_TOKEN = 4.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def group_paragraphs(lines: list[Line]) -> list[list[Line]]:
    """Split a section's lines into paragraphs using spacing and indentation."""
    if not lines:
        return []

    gaps = [
        curr.y - prev.y
        for prev, curr in pairwise(lines)
        if curr.page == prev.page and curr.column == prev.column and curr.y > prev.y
    ]
    typical_gap = _median(gaps)
    left_margin = min(line.x0 for line in lines)

    paragraphs: list[list[Line]] = [[lines[0]]]
    for prev, curr in pairwise(lines):
        same_flow = curr.page == prev.page and curr.column == prev.column
        gap = curr.y - prev.y

        if not same_flow:
            # A column or page break is a safe boundary: it is either a real
            # paragraph break or a place a reader already pauses.
            new_paragraph = True
        elif typical_gap and gap > typical_gap * _PARAGRAPH_GAP_RATIO:
            # A gap noticeably wider than this section's own line spacing.
            new_paragraph = True
        elif curr.x0 > left_margin + _INDENT_POINTS and prev.text.endswith((".", "!", "?")):
            # A first-line indent following a completed sentence.
            new_paragraph = True
        else:
            new_paragraph = False

        if new_paragraph:
            paragraphs.append([curr])
        else:
            paragraphs[-1].append(curr)
    return paragraphs


def _split_by_chars(text: str, max_tokens: int) -> list[str]:
    """Hard-split oversized text, preferring a sentence boundary near the end.

    The backstop of last resort. It only runs on a single paragraph that is
    itself over budget -- in practice a reference list or a flattened table.
    """
    window = max(1, int(max_tokens * _CHARS_PER_TOKEN))
    pieces: list[str] = []
    remaining = text.strip()

    while len(remaining) > window:
        head = remaining[:window]
        # Look for a sentence end in the last quarter of the window; anywhere
        # earlier would waste too much of the budget.
        boundary = -1
        for match in _SENTENCE_END.finditer(head):
            if match.start() > window * 0.75:
                boundary = match.end()
        if boundary <= 0:
            boundary = head.rfind(" ")
        if boundary <= 0:
            boundary = window
        pieces.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()

    if remaining:
        pieces.append(remaining)
    return [p for p in pieces if p]


def section_paragraphs(section: Section) -> list[str]:
    """Prose paragraphs first, then captions and in-figure labels.

    Furniture is appended rather than dropped: 'Figure 2: (left) Scaled
    Dot-Product Attention...' is genuinely useful when explaining the figure.
    It just must not sit in the middle of the sentence it interrupted on the
    page.
    """
    paragraphs = [join_lines(group) for group in group_paragraphs(section.lines)]
    if section.furniture:
        paragraphs.extend(
            join_lines(group) for group in group_paragraphs(section.furniture)
        )
    return [p for p in paragraphs if p]


def split_section_text(section: Section, max_tokens: int) -> list[str]:
    """Return the text of each chunk this section produces, in order."""
    paragraphs = section_paragraphs(section)
    if not paragraphs:
        return []

    whole = " ".join(paragraphs)
    if estimate_tokens(whole) <= max_tokens:
        return [whole]

    chunks: list[str] = []
    buffer: list[str] = []
    buffer_chars = 0

    def flush() -> None:
        nonlocal buffer, buffer_chars
        if buffer:
            chunks.append(" ".join(buffer))
            buffer = []
            buffer_chars = 0

    for paragraph in paragraphs:
        if estimate_tokens(paragraph) > max_tokens:
            # Oversized on its own: flush what we have, then char-split it.
            flush()
            chunks.extend(_split_by_chars(paragraph, max_tokens))
            continue

        # Budget on the joined length, including the separator this paragraph
        # would add. Summing per-paragraph estimates ignores those spaces and
        # lets a chunk drift a few tokens over the stated maximum.
        candidate = buffer_chars + (1 if buffer else 0) + len(paragraph)
        if buffer and estimate_tokens_from_chars(candidate) > max_tokens:
            flush()
            candidate = len(paragraph)
        buffer.append(paragraph)
        buffer_chars = candidate

    flush()
    return chunks


def chunk_paper(paper_id: str, sections: list[Section], max_tokens: int) -> list[Chunk]:
    """Turn a paper's sections into stably-identified chunks."""
    keys = disambiguate([section_key(s.number, s.title) for s in sections])

    chunks: list[Chunk] = []
    for section, key in zip(sections, keys, strict=True):
        texts = split_section_text(section, max_tokens)
        n_parts = len(texts)
        for index, text in enumerate(texts, start=1):
            chunks.append(
                Chunk(
                    id=chunk_id(paper_id, key, part=index, n_parts=n_parts),
                    paper_id=paper_id,
                    section_number=section.number,
                    section_title=section.title,
                    text=text,
                    token_count=estimate_tokens(text),
                    token_counter="estimate",
                    char_count=len(text),
                    part=index,
                    n_parts=n_parts,
                    page_start=section.page_start,
                    page_end=section.page_end,
                )
            )
    return chunks
