"""Stable, human-readable section and chunk identifiers.

Chunk ids are eval labels. They must come out identical across runs and
machines for a fixed PDF, so every rule here is deliberate:

* Derived from the section's own number and title -- never from a chunk's
  position in a list. Adding a section near the front of a paper moves no other id.
* Slugs are ASCII-folded and cut at a fixed word/character budget, so a unicode
  dash or a trailing footnote marker in the PDF cannot shift them.
* Repeated titles are disambiguated by document order (``_2``, ``_3``), which
  is stable for a fixed input.
* ``_p{n}`` appears only when a section actually splits; the common case reads
  like a table of contents.

What may legitimately move an id: a new arXiv version (pin it) and a change to
extraction, sectioning or chunking (bump ``CHUNKER_VERSION``, which is written
into every ingested paper so a relabel is visible in a diff, never silent).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

# Bump ONLY when a code change can alter chunk boundaries or ids.
#   2: footnotes move to the section that cites them.
#   3: text tables captioned "Figure N" are kept instead of dropped as figure labels.
CHUNKER_VERSION = "3"

_MAX_SLUG_WORDS = 6
_MAX_SLUG_CHARS = 48
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """'Scaled Dot-Product Attention' -> 'scaled_dot_product_attention'."""
    # NFKD + ASCII drop folds accents and ligatures the PDF may or may not have
    # emitted ('ﬁ' vs 'fi' must not produce two different ids).
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    words = [w for w in _NON_SLUG.split(folded) if w][:_MAX_SLUG_WORDS]
    if not words:
        return "untitled"
    kept: list[str] = []
    length = 0
    for word in words:
        # Cut on a word boundary: a mid-word cut is ugly and collides more easily.
        extra = len(word) + (1 if kept else 0)
        if kept and length + extra > _MAX_SLUG_CHARS:
            break
        kept.append(word[:_MAX_SLUG_CHARS])
        length += extra
    return "_".join(kept)


def section_id(number: str | None, title: str) -> str:
    """Outline id: 'sec_3.2' for numbered sections, 'sec_abstract' otherwise."""
    return f"sec_{number}" if number else f"sec_{slugify(title)}"


def section_key(number: str | None, title: str) -> str:
    """The section part of a chunk id: 'sec_3.2.1_scaled_dot_product_attention', 'sec_references'.

    The number is kept verbatim (dots included): it is how a reader names the
    section out loud and what ``section_filter`` matches against.
    """
    slug = slugify(title)
    return f"sec_{number}_{slug}" if number else f"sec_{slug}"


def disambiguate(keys: Sequence[str]) -> list[str]:
    """Suffix repeated keys with _2, _3 ... in document order."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for key in keys:
        seen[key] = seen.get(key, 0) + 1
        out.append(key if seen[key] == 1 else f"{key}_{seen[key]}")
    return out


def chunk_id(paper_id: str, key: str, part: int | None = None) -> str:
    """'{paper_id}:{key}' plus '_p{part}' only when the section was split."""
    base = f"{paper_id}:{key}"
    return base if part is None else f"{base}_p{part}"
