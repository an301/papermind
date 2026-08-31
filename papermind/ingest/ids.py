"""Stable, human-readable chunk identifiers.

These IDs are eval labels. They must be identical across runs, machines, and
re-downloads, so every rule here is deliberate:

- Derived from the paper id, the section's own number, and its title -- never
  from a chunk's position in a list. Insert a section at the front of a paper
  and every other ID stays put.
- Slugs are ASCII-folded and truncated at a fixed word/char budget, so a
  unicode dash or a trailing footnote marker in the PDF cannot shift them.
- Collisions are resolved by document order (`_2`, `_3`), which is stable for a
  fixed input PDF.
- Part suffixes (`_p1`, `_p2`) appear only when a section actually splits.

The things that CAN legitimately change an ID are the arXiv version (a v2 may
retitle a section) and CHUNKER_VERSION. Both are recorded in the output so a
break is visible in a diff rather than silent. Pin the arXiv version and treat
a CHUNKER_VERSION bump as a re-labelling event.
"""

from __future__ import annotations

import re
import unicodedata

# Bump ONLY when a change alters chunk boundaries or ID construction. It is
# written into every output file; a changed value means previously emitted
# labels may no longer refer to the same text.
CHUNKER_VERSION = 1

_MAX_SLUG_WORDS = 6
_MAX_SLUG_CHARS = 48

# Trailing junk PDFs attach to headings: footnote daggers, asterisks, dots.
_TRAILING_JUNK = re.compile(r"[\s.·*†‡§¶]+$")
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(title: str) -> str:
    """Fold a section title to a short, stable, lowercase ASCII slug.

    'Scaled Dot-Product Attention' -> 'scaled_dot_product_attention'
    """
    cleaned = _TRAILING_JUNK.sub("", title)
    # NFKD + ASCII drop folds ligatures and accents that PDF extraction emits
    # inconsistently ('ﬁ' vs 'fi' would otherwise be two different IDs).
    folded = unicodedata.normalize("NFKD", cleaned)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()

    words = [w for w in _NON_SLUG.split(ascii_only) if w]
    if not words:
        return "untitled"

    slug = "_".join(words[:_MAX_SLUG_WORDS])
    if len(slug) > _MAX_SLUG_CHARS:
        # Truncate on a word boundary so the slug stays readable, and never
        # mid-word -- a char-truncated slug is both ugly and easier to collide.
        truncated: list[str] = []
        length = 0
        for word in words[:_MAX_SLUG_WORDS]:
            if length + len(word) > _MAX_SLUG_CHARS:
                break
            truncated.append(word)
            length += len(word) + 1
        slug = "_".join(truncated) or words[0][:_MAX_SLUG_CHARS]
    return slug


def section_key(number: str | None, title: str) -> str:
    """The section-identifying part of an ID: `sec_3.2_attention`, `sec_abstract`.

    The section number is kept verbatim (dots and all) because it is how a
    reader refers to the section out loud, and how the frontend links to it.
    """
    slug = slugify(title)
    return f"sec_{number}_{slug}" if number else f"sec_{slug}"


def chunk_id(paper_id: str, key: str, part: int = 1, n_parts: int = 1) -> str:
    """Compose the final ID. Part suffix is omitted for unsplit sections.

    A section that fits in one chunk is `...:sec_3.2_attention`; one that splits
    is `...:sec_3.2_attention_p1`, `_p2`, ... Emitting `_p1` on a single-part
    section would make the common case noisier for no gain.
    """
    base = f"{paper_id}:{key}"
    return base if n_parts <= 1 else f"{base}_p{part}"


def disambiguate(keys: list[str]) -> list[str]:
    """Append `_2`, `_3` to repeated section keys, in document order.

    Unnumbered subsections collide often ('Setup' under two different sections).
    Document order is the only tiebreak that is stable for a fixed PDF.
    """
    seen: dict[str, int] = {}
    out: list[str] = []
    for key in keys:
        seen[key] = seen.get(key, 0) + 1
        out.append(key if seen[key] == 1 else f"{key}_{seen[key]}")
    return out
