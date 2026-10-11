"""Raw Semantic Scholar JSON -> ``ScholarPaper``.

Pure functions, so the reshaping rules are tested without HTTP. Everything here
is defensive: S2 returns ``null`` for most fields on some records (abstracts are
withheld for licensing reasons even when the website shows one), references to
papers outside the corpus have no ``paperId``, and author lists on large
collaborations run to hundreds of names.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from papermind.schemas.tools import ScholarPaper

MAX_AUTHORS = 6
# Long enough for the writer to tell what a paper is about, short enough that a
# page of ten references does not dominate the agent's context.
ABSTRACT_CHARS = 1200


def trim_text(text: str | None, limit: int = ABSTRACT_CHARS) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Back off to a word boundary unless that would throw away most of the text.
    space = cut.rfind(" ")
    if space > limit * 0.8:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"


def _str_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _int_or_none(value: Any) -> int | None:
    # bool is an int subclass; a stray true/false is not a count.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def to_scholar_paper(raw: Mapping[str, Any] | None) -> ScholarPaper | None:
    """Reshape one S2 paper object; None when it has no S2 id (nothing to cite or look up)."""
    if not raw:
        return None
    s2_id = _str_or_none(raw.get("paperId"))
    if s2_id is None:
        return None

    authors = [
        name
        for author in raw.get("authors") or []
        if isinstance(author, Mapping) and (name := _str_or_none(author.get("name")))
    ][:MAX_AUTHORS]

    tldr = raw.get("tldr")
    external = raw.get("externalIds")
    return ScholarPaper(
        s2_id=s2_id,
        title=_str_or_none(raw.get("title")) or "(untitled)",
        year=_int_or_none(raw.get("year")),
        authors=authors,
        venue=_str_or_none(raw.get("venue")),
        abstract=trim_text(_str_or_none(raw.get("abstract"))),
        tldr=_str_or_none(tldr.get("text")) if isinstance(tldr, Mapping) else None,
        citation_count=_int_or_none(raw.get("citationCount")),
        url=_str_or_none(raw.get("url")),
        arxiv_id=_str_or_none(external.get("ArXiv")) if isinstance(external, Mapping) else None,
    )


def papers_from_graph_page(page: Mapping[str, Any] | None, wrapper: str) -> list[ScholarPaper]:
    """Unwrap a references/citations page (``citedPaper`` / ``citingPaper`` entries)."""
    out: list[ScholarPaper] = []
    seen: set[str] = set()
    for entry in (page or {}).get("data") or []:
        if not isinstance(entry, Mapping):
            continue
        paper = to_scholar_paper(entry.get(wrapper))
        if paper is not None and paper.s2_id not in seen:
            seen.add(paper.s2_id)
            out.append(paper)
    return out


def most_cited_first(papers: Iterable[ScholarPaper]) -> list[ScholarPaper]:
    # sorted() is stable, so ties keep S2's order.
    return sorted(papers, key=lambda p: -(p.citation_count if p.citation_count is not None else -1))
