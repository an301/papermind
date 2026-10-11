"""Sections -> chunks.

Splitting is layered, cheapest structure first:

1. A section that fits ``max_chunk_tokens`` is one chunk. This is the common
   case and the reason chunk ids read like a table of contents.
2. Otherwise its paragraphs are packed, aiming for equal-sized parts rather
   than greedily filling each one (greedy packing leaves a runt at the end).
3. A single paragraph over budget is split at sentence boundaries; only a
   sentence longer than the whole budget is cut at a word boundary.
4. Any part under ~400 characters is merged into a neighbour in the same
   section: a two-sentence chunk is a poor retrieval unit and a noisy eval label.

Paragraph boundaries come from line geometry (block changes, first-line
indents) because extracted PDF text has no blank lines. Every chunk keeps
the lines it came from, so it can carry page-level boxes for highlighting.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from papermind.ingest.extract import Line
from papermind.ingest.ids import chunk_id, disambiguate, section_key
from papermind.ingest.sections import SectionText
from papermind.schemas.paper import Box, Chunk

CHARS_PER_TOKEN = 4
MIN_CHUNK_CHARS = 400

_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
_TRAILING_FRAGMENT = re.compile(r"([^\W\d_]+)-$")
_LEADING_FRAGMENT = re.compile(r"^([^\W\d_]+)")
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'”’)\]]*\s+(?=[\"'“‘(\[]?[A-Z0-9])")
# Words after which a period does not end a sentence.
_ABBREVIATIONS = {
    "e.g",
    "i.e",
    "et al",
    "al",
    "fig",
    "figs",
    "eq",
    "eqs",
    "sec",
    "secs",
    "tab",
    "ref",
    "refs",
    "vs",
    "cf",
    "resp",
    "approx",
    "no",
    "dr",
    "mr",
    "ms",
    "prof",
    "etc",
}
# Line-end compounds with these prefixes keep their hyphen when the document
# offers no evidence either way ('non-' + 'linear').
_HYPHEN_PREFIXES = {"non", "cross", "semi", "well", "pre", "post", "anti"}
# ...and these always do: TeX never breaks a word after 'self' (there is no
# such syllable boundary in technical English), so 'self-' at a line end is
# always the compound's own hyphen ('self-' + 'attention').
_ALWAYS_COMPOUND = frozenset({"self"})
# Right-hand fragments that are word endings, not words: 'parameter-' +
# 'ization' is TeX hyphenation even though 'parameter' is a word.
_SUFFIXES = frozenset(
    {
        "ization", "isation", "izing", "ising", "ation", "ition", "tion", "sion", "ment", "ness", "ing",
        "ized", "ised", "izes", "ises", "ize", "ise", "able", "ible", "ably", "ibly", "ities", "ity",
        "ively", "ive", "ously", "ous", "ally", "ence", "ance", "ency", "ancy", "ism", "ist", "ful",
        "less", "ers", "est", "ed", "es", "er", "al", "ly", "ary", "ory", "ial", "ical", "ically",
        "ification", "ication", "ent", "ents", "iable", "ibility", "ability", "ative", "ational",
        "ings", "ments", "ations", "itions", "ular", "ularity",
    }
)  # fmt: skip
# Prefixes TeX happily breaks after ('multi-' + 'plicative', 'inter-' +
# 'pretable'). Unlike free words ('human-' + 'annotated'), a line ending in one
# of these says nothing about whether the hyphen is real.
_BOUND_PREFIXES = frozenset(
    {"multi", "inter", "intra", "super", "hyper", "trans", "counter", "under", "over", "micro", "macro",
     "meta", "poly", "mono", "auto", "para", "infra", "ultra", "extra", "circum"}
)  # fmt: skip
# Inflections tried when asking whether a fragment is a word of this paper:
# 'aligned' counts as known when the paper says 'aligning'.
_INFLECTIONS = ("", "s", "es", "ed", "d", "ing", "er", "ers", "ment", "ments", "ly", "e")
# A bullet starts a paragraph of its own; so does a numbered reference entry
# ('[12] ...'), but only after the previous entry ended -- an in-text citation
# can wrap to the start of a line too ('on ImageNet' / '[36] to show').
_BULLET_START = re.compile(r"^[•◦▪‣∙]\s?")
_REFERENCE_START = re.compile(r"^\[\d{1,3}\]\s")
# 'https://github.com/' + 'tensorflow/tensor2tensor', 'https://blog.' + 'openai.com':
# a URL broken across lines.
_URL_TAIL = re.compile(r"(?:https?://|www\.)\S*[/.]$")


def estimate_tokens(text: str) -> int:
    """~chars/4: close enough for budgeting, and needs no tokenizer at ingest time."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


# ---------------------------------------------------------------- hyphenation


class Vocabulary:
    """Word counts for one document, used to decide line-end hyphens.

    'atten-' + 'tion' must become 'attention' but 'self-' + 'attention' must
    stay 'self-attention'; no fixed rule gets both right. The paper itself
    usually does: whichever form occurs elsewhere in it wins. Fragments at
    hyphenated line ends are excluded from the counts -- they are the
    question, not the evidence.
    """

    def __init__(self, lines: Iterable[Line]) -> None:
        self.counts: Counter[str] = Counter()
        previous_hyphenated = False
        for line in lines:
            words = _WORD.findall(line.text.lower())
            if previous_hyphenated and words:
                words = words[1:]
            hyphenated = line.text.rstrip().endswith("-")
            if hyphenated and words:
                words = words[:-1]
            for word in words:
                self.counts[word] += 1
                parts = word.split("-")
                if len(parts) > 1:
                    self.counts.update(parts)
                    self.counts.update(f"{a}-{b}" for a, b in pairwise(parts))
            previous_hyphenated = hyphenated

    def _stems(self, word: str) -> set[str]:
        stems = {word}
        for suffix in _INFLECTIONS[1:]:
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                stems.add(word[: -len(suffix)])
        return stems

    def known(self, word: str) -> bool:
        """Does this word, or an inflection of its stem, occur in the paper?"""
        return any(self.counts[stem + ending] for stem in self._stems(word) for ending in _INFLECTIONS)

    def only_closed(self, left: str, right: str) -> bool:
        """Does the paper write 'left' + 'right' closed up, and never hyphenated?"""
        left_l, right_l = left.lower(), right.lower()
        return self.counts[left_l + right_l] > 0 and self.counts[f"{left_l}-{right_l}"] == 0

    def keep_hyphen(self, left: str, right: str, *, compound_follows: bool = False) -> bool:
        """Is 'left-' + 'right' at a line end a real hyphen, or TeX's?"""
        left_l, right_l = left.lower(), right.lower()
        if compound_follows or left_l in _ALWAYS_COMPOUND:
            # 'end-' + 'to-end', 'left-to-' + 'right': the right half carries
            # hyphens of its own, so this one belongs to the same compound.
            return True
        hyphenated = self.counts[f"{left_l}-{right_l}"]
        closed = self.counts[left_l + right_l]
        if hyphenated != closed:
            return hyphenated > closed
        # Inflected evidence: 'understand' elsewhere settles 'under-' + 'standing'.
        closed_known = any(self.known(left_l + stem) for stem in self._stems(right_l))
        hyphen_known = any(
            self.counts[f"{left_l}-{stem}{e}"] for stem in self._stems(right_l) for e in _INFLECTIONS
        )
        if closed_known != hyphen_known:
            return hyphen_known
        if left_l in _HYPHEN_PREFIXES and self.known(right_l):
            return True
        if right_l in _SUFFIXES or (right_l.endswith("s") and right_l[:-1] in _SUFFIXES):
            return False
        # Neither form appears elsewhere. TeX's own break points rarely leave
        # a real word on both sides ('multi' + 'plicative', 'transduc' +
        # 'tion'); a compound broken at its hyphen does ('sequence' +
        # 'aligned', 'greedily' + 'sampled'). Common words almost always also
        # appear unbroken somewhere in a paper and were settled above, so this
        # only decides rare compounds.
        if len(left_l) < 3 or len(right_l) < 3:
            return False
        # TeX never breaks a word just after a closing '-ly' ('deep-ly', not
        # 'deeply-'), so an adverb at a line end is a compound's own hyphen
        # ('fully-' + 'connected', 'deeply-' + 'supervised').
        adverb = left_l.endswith("ly") and len(left_l) >= 5
        if self.known(right_l):
            return adverb or self.known(left_l)
        if adverb and len(right_l) >= 4:
            return True
        # The right half is not a word of this paper: rare compounds of a free
        # word ('global-' + 'availability') still are, TeX breaks after a bound
        # prefix are not.
        return (
            self.counts[left_l] >= 2
            and len(left_l) >= 5
            and len(right_l) >= 4
            and left_l not in _BOUND_PREFIXES
        )


def join_lines(texts: Sequence[str], vocab: Vocabulary) -> tuple[str, list[tuple[int, int]]]:
    """Join a paragraph's lines into one string, repairing line-end hyphens.

    Returns the text and each line's (start, end) character range in it.
    """
    out = ""
    ranges: list[tuple[int, int]] = []
    for raw in texts:
        text = raw.strip()
        start = len(out)
        if out:
            prev = out
            nxt = text
            fragment = _TRAILING_FRAGMENT.search(prev)
            lead = _LEADING_FRAGMENT.match(nxt)
            if prev.endswith(("—", "–")):
                pass  # em/en dash: LaTeX sets these without spaces
            elif fragment and lead and (nxt[:1].islower() or nxt[:1].isupper()):
                left, right = fragment.group(1), lead.group(1)
                compound = nxt[lead.end() : lead.end() + 1] == "-"
                if nxt[:1].islower():
                    drop = not vocab.keep_hyphen(left, right, compound_follows=compound)
                else:
                    # 'Bi-' + 'LSTM' keeps its hyphen, but a name TeX broke
                    # ('Le-' + 'Cun', 'De-' + 'BERTa') is spelled closed
                    # everywhere else in the paper.
                    drop = not compound and vocab.only_closed(left, right)
                if drop:
                    out = out[:-1]
                    # The previous line's range must not claim the first
                    # character of this one now that the hyphen is gone.
                    prev_start, _ = ranges[-1]
                    ranges[-1] = (prev_start, len(out))
                start = len(out)
            elif prev.endswith("-") and len(prev) > 1 and (prev[-2].isalnum()) and nxt[:1].isalnum():
                pass  # 'GPT-' + '3': a real hyphen, no space
            elif _URL_TAIL.search(prev) and (prev.endswith("/") or nxt[:1].islower()):
                pass
            else:
                out += " "
                start = len(out)
        out += text
        ranges.append((start, len(out)))
    return out, ranges


# ---------------------------------------------------------------- paragraphs


@dataclass
class Paragraph:
    lines: list[Line]
    text: str
    ranges: list[tuple[int, int]]  # per line, into ``text``


_TRAILING_MARK = re.compile(r"(?:\^\{[^}]*\}|\^\S+)$")


def _ends_sentence(text: str) -> bool:
    # 'context to its left.^4': the footnote mark comes after the full stop.
    text = _TRAILING_MARK.sub("", text.rstrip()).rstrip()
    return text.endswith((".", "!", "?", ":", ".”", '."', ".)"))


def _paragraph_break(prev: Line, cur: Line, region_width: float) -> bool:
    """Does ``cur`` start a new paragraph? Extracted text has no blank lines,
    so this is decided from geometry.

    PyMuPDF block boundaries are not used: a reference entry's first line and
    its hanging-indented continuation are routinely separate blocks, and a
    block per display equation splits sentences. The signals that hold across
    templates are extra vertical space (NeurIPS, ICLR), a first-line indent
    after a sentence end (ICML, CVPR set paragraphs with no extra space), a
    short last line, and list/reference markers.
    """
    if cur.kind != prev.kind or cur.float_id != prev.float_id:
        return True
    if cur.kind in ("caption", "table"):
        return False  # a caption, and a table body, each read as one unit
    size = max(prev.size, cur.size, 1.0)
    indented = cur.indent > 0.6 * size and cur.indent > prev.indent + 0.6 * size
    short_end = _ends_sentence(prev.text) and prev.width < 0.85 * region_width
    if _BULLET_START.match(cur.text):
        return True
    if (_REFERENCE_START.match(cur.text) or cur.run_in) and _ends_sentence(prev.text):
        return True
    if (cur.page, cur.band, cur.column) != (prev.page, prev.band, prev.column):
        # A column or page break is a paragraph break only if it looks like one:
        # a paragraph that flows into the next column continues mid-sentence.
        return indented or short_end
    if cur.y0 - prev.y1 > 0.45 * size:
        return True
    return (indented and _ends_sentence(prev.text)) or short_end


def split_paragraphs(lines: Sequence[Line], vocab: Vocabulary) -> list[Paragraph]:
    widths: dict[tuple[int, int, int], float] = {}
    for line in lines:
        widths[line.region] = max(widths.get(line.region, 0.0), line.width)
    groups: list[list[Line]] = []
    for line in lines:
        if not line.text.strip():
            continue
        if groups and not _paragraph_break(groups[-1][-1], line, widths[groups[-1][-1].region]):
            groups[-1].append(line)
        else:
            groups.append([line])
    paragraphs = []
    for group in groups:
        text, ranges = join_lines([ln.text for ln in group], vocab)
        paragraphs.append(Paragraph(lines=group, text=text, ranges=ranges))
    return paragraphs


# ---------------------------------------------------------------- splitting and packing


@dataclass
class _Piece:
    """A paragraph, or a sentence-aligned slice of one."""

    paragraph: int
    text: str
    lines: list[Line]


@dataclass
class _Part:
    pieces: list[_Piece] = field(default_factory=list)

    @property
    def text(self) -> str:
        out = ""
        for prev, piece in zip([None, *self.pieces[:-1]], self.pieces, strict=True):
            if prev is not None:
                out += " " if prev.paragraph == piece.paragraph else "\n\n"
            out += piece.text
        return out

    @property
    def size(self) -> int:
        return len(self.text)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        before = text[start : match.start()].rstrip(".!?\"'”’)]").split()
        last = before[-1].lower().rstrip(".") if before else ""
        # 'et al. (2017)', 'Fig. 3', single initials ('A. Vaswani') do not end sentences.
        if last in _ABBREVIATIONS or (len(last) == 1 and last.isalpha()):
            continue
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _split_long_span(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Cut one over-long sentence at word boundaries. The last resort."""
    out: list[tuple[int, int]] = []
    while end - start > max_chars:
        cut = text.rfind(" ", start, start + max_chars)
        if cut <= start:
            cut = start + max_chars
        out.append((start, cut))
        start = cut + 1 if text[cut : cut + 1] == " " else cut
    out.append((start, end))
    return out


def _split_paragraph(index: int, paragraph: Paragraph, max_chars: int) -> list[_Piece]:
    text = paragraph.text
    if len(text) <= max_chars:
        return [_Piece(index, text, paragraph.lines)]
    spans: list[tuple[int, int]] = []
    for a, b in _sentence_spans(text):
        spans.extend(_split_long_span(text, a, b, max_chars) if b - a > max_chars else [(a, b)])

    parts = math.ceil(len(text) / max_chars)
    target = len(text) / parts
    groups: list[tuple[int, int]] = []
    group_start, group_end = spans[0]
    for a, b in spans[1:]:
        if b - group_start > max_chars or group_end - group_start >= target:
            groups.append((group_start, group_end))
            group_start = a
        group_end = b
    groups.append((group_start, group_end))

    pieces = []
    for a, b in groups:
        lines = [
            ln for ln, (la, lb) in zip(paragraph.lines, paragraph.ranges, strict=True) if la < b and lb > a
        ]
        pieces.append(_Piece(index, text[a:b].strip(), lines))
    return pieces


def _pack(pieces: list[_Piece], max_chars: int) -> list[_Part]:
    total = sum(len(p.text) + 2 for p in pieces)
    target = total / max(1, math.ceil(total / max_chars))
    parts: list[_Part] = [_Part()]
    for piece in pieces:
        current = parts[-1]
        if current.pieces and (current.size + 2 + len(piece.text) > max_chars or current.size >= target):
            previous = current.pieces[-1]
            if len(current.pieces) > 1 and _caption_of(previous, piece):
                # A table body is unreadable without its caption: carry the
                # caption over to the part the body starts.
                current.pieces.pop()
                parts.append(_Part([previous]))
            else:
                parts.append(_Part())
        parts[-1].pieces.append(piece)
    return _merge_small(parts, max_chars)


def _caption_of(caption: _Piece, body: _Piece) -> bool:
    first, other = caption.lines[0], body.lines[0]
    return first.kind == "caption" and other.kind == "table" and first.float_id == other.float_id


def _merge_small(parts: list[_Part], max_chars: int) -> list[_Part]:
    """Fold parts under MIN_CHUNK_CHARS into the smaller neighbour.

    The merged part may exceed the budget by up to MIN_CHUNK_CHARS; a chunk a
    little over budget is a far better retrieval unit than a runt.
    """
    changed = True
    while changed and len(parts) > 1:
        changed = False
        for i, part in enumerate(parts):
            if part.size >= MIN_CHUNK_CHARS:
                continue
            neighbours = [j for j in (i - 1, i + 1) if 0 <= j < len(parts)]
            j = min(neighbours, key=lambda n: parts[n].size)
            if parts[j].size + part.size > max_chars + MIN_CHUNK_CHARS:
                continue
            first, second = (j, i) if j < i else (i, j)
            parts[first] = _Part(parts[first].pieces + parts[second].pieces)
            del parts[second]
            changed = True
            break
    return parts


# ---------------------------------------------------------------- boxes


def _boxes(lines: Sequence[Line]) -> list[Box]:
    """Merge line rectangles into block-level boxes, per page and column.

    Consecutive lines merge while they stay in one column and start no more
    than a line height below the box so far; captions, tables and footnotes
    get their own boxes because they sit elsewhere on the page than the prose
    around them.

    Overlap is allowed: a row carrying a fraction, a sum or a superscript has
    a bbox taller than the line pitch, so it overlaps its neighbours, and
    refusing to merge then leaves a stack of overlapping slivers that the UI
    would paint twice. Only a step back up the page -- the reading order
    jumped -- starts a new box.
    """
    boxes: list[list[float]] = []
    keys: list[tuple[int, int, int, str, int | None]] = []
    last: Line | None = None
    for line in lines:
        key = (line.page, line.band, line.column, line.kind, line.float_id)
        y0, y1 = line.box_y0, line.box_y1
        if (
            last is not None
            and keys
            and keys[-1] == key
            and y0 >= boxes[-1][1] - 1.0
            and y0 - boxes[-1][3] <= 1.0 * max(line.size, last.size)
        ):
            box = boxes[-1]
            box[0], box[1], box[2], box[3] = (
                min(box[0], line.x0),
                min(box[1], y0),
                max(box[2], line.x1),
                max(box[3], y1),
            )
        else:
            boxes.append([line.x0, y0, line.x1, y1])
            keys.append(key)
        last = line
    return [
        Box(page=key[0], x0=round(b[0], 1), y0=round(b[1], 1), x1=round(b[2], 1), y1=round(b[3], 1))
        for key, b in zip(keys, boxes, strict=True)
    ]


# ---------------------------------------------------------------- entry point


def chunk_sections(paper_id: str, sections: Sequence[SectionText], max_tokens: int) -> list[Chunk]:
    """Turn a paper's sections into stably identified chunks."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    vocab = Vocabulary(line for text in sections for line in text.lines)
    keys = disambiguate([section_key(s.section.number, s.section.title) for s in sections])
    chunks: list[Chunk] = []
    for text, key in zip(sections, keys, strict=True):
        paragraphs = split_paragraphs(text.lines, vocab)
        if not paragraphs:
            continue
        whole = "\n\n".join(p.text for p in paragraphs)
        if len(whole) <= max_chars:
            parts = [_Part([_Piece(i, p.text, p.lines) for i, p in enumerate(paragraphs)])]
        else:
            pieces = [piece for i, p in enumerate(paragraphs) for piece in _split_paragraph(i, p, max_chars)]
            parts = _pack(pieces, max_chars)
        split = len(parts) > 1
        for number, part in enumerate(parts, start=1):
            lines = [line for piece in part.pieces for line in piece.lines]
            body = part.text
            chunks.append(
                Chunk(
                    chunk_id=chunk_id(paper_id, key, number if split else None),
                    paper_id=paper_id,
                    section_id=text.section.section_id,
                    section_number=text.section.number,
                    section_title=text.section.title,
                    part=number if split else None,
                    text=body,
                    token_count=estimate_tokens(body),
                    page_start=min(line.page for line in lines),
                    page_end=max(line.page for line in lines),
                    boxes=_boxes(_dedupe(lines)),
                )
            )
    return chunks


def _dedupe(lines: Iterable[Line]) -> list[Line]:
    # A line straddling a sentence split belongs to both pieces of one part.
    seen: set[int] = set()
    out = []
    for line in lines:
        if id(line) not in seen:
            seen.add(id(line))
            out.append(line)
    return out
