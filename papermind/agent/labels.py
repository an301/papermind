"""Citation labels: what the writer may cite, and as what.

Two kinds of label, one syntax:

* [S1], [S2] ... are passages of the open paper. The agent names chunks by id
  in ``compose_answer``; they are labelled in the order given, and each maps to
  a ``SourceRef`` the UI turns into a citation chip and a bracket on the page.
* [P1], [P2] ... are other papers the agent looked up on Semantic Scholar.
  Each maps to an ``ExternalRef``; the UI renders the marker as "Bahdanau et
  al., 2014" with a card, and never draws a bracket for it.

Labels are short and unambiguous for the model. Anything the writer cites that
is not in the table it was given is stripped before the reader sees the text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from papermind.schemas.events import ExternalRef, SourceRef
from papermind.schemas.tools import RetrievedChunk, ScholarPaper

SNIPPET_CHARS = 240
# ComposeAnswerArgs.source_chunk_ids allows at most 8, external_papers at most 5.
MAX_SOURCES = 8
MAX_EXTERNAL = 5

# "[S1]", "[P2]" and the grouped forms a model sometimes writes anyway: "[S1, S3]", "[S1; P1]".
_LABEL = r"[SP]\d+"
_CITATION = re.compile(rf"([ \t]*)\[({_LABEL}(?:\s*[,;]\s*{_LABEL})*)\]")


@dataclass(frozen=True)
class LabelledSource:
    ref: SourceRef
    text: str

    @property
    def label(self) -> str:
        return self.ref.label


@dataclass(frozen=True)
class LabelledExternal:
    ref: ExternalRef
    paper: ScholarPaper

    @property
    def label(self) -> str:
        return self.ref.label


# ---------------------------------------------------------------- passages of the open paper


def top_retrieved(seen: Mapping[str, RetrievedChunk], k: int) -> list[RetrievedChunk]:
    """Best-ranked chunks across every search this turn: all rank-1 hits first, then rank 2, ..."""
    order = {cid: i for i, cid in enumerate(seen)}
    ranked = sorted(seen.values(), key=lambda c: (c.rank, order[c.chunk_id]))
    return ranked[:k]


def resolve_sources(
    requested: list[str] | None, seen: Mapping[str, RetrievedChunk], *, fallback_k: int
) -> tuple[list[RetrievedChunk], list[str]]:
    """The chunks the writer gets, and the requested ids that were dropped.

    ``requested`` is ``compose_answer.source_chunk_ids``, or None when the turn
    ended without a valid compose_answer. Ids not retrieved this turn are
    dropped: the writer must only cite text it is given. When nothing usable
    was requested -- no compose_answer, or every id was invented -- the top
    retrieved chunks stand in. An explicitly empty list is respected (small
    talk, or a question the paper does not cover).
    """
    if requested is None:
        return top_retrieved(seen, fallback_k), []
    unique = list(dict.fromkeys(requested))
    picked = [seen[cid] for cid in unique if cid in seen]
    dropped = [cid for cid in unique if cid not in seen]
    if unique and not picked:
        return top_retrieved(seen, fallback_k), dropped
    return picked[:MAX_SOURCES], dropped


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= SNIPPET_CHARS:
        return text
    cut = text[:SNIPPET_CHARS].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def label_sources(chunks: Iterable[RetrievedChunk]) -> list[LabelledSource]:
    return [
        LabelledSource(
            ref=SourceRef(
                label=f"S{i}",
                chunk_id=c.chunk_id,
                section_number=c.section_number,
                section_title=c.section_title,
                page_start=c.page_start,
                page_end=c.page_end,
                snippet=_snippet(c.text),
            ),
            text=c.text,
        )
        for i, c in enumerate(chunks, start=1)
    ]


# ---------------------------------------------------------------- other papers


def resolve_external(
    requested: list[str] | None, seen: Mapping[str, ScholarPaper], lookups: Sequence[ScholarPaper]
) -> list[ScholarPaper]:
    """The looked-up papers the writer may cite as [P#], most important first.

    ``requested`` is ``compose_answer.external_papers``, or None when the agent
    never chose (no compose_answer, or the field left out). Unchosen, the best
    match of each ``lookup_paper`` call stands in: the agent looked the paper
    up because the question is about it, and a lookup the writer never sees is
    wasted. Reference and citation lists do not stand in; most of their entries
    are only background. As with passages, ids not seen this turn are dropped,
    an all-invented list falls back, and an explicitly empty list is respected.
    """
    fallback = list({p.s2_id: p for p in lookups}.values())[:MAX_EXTERNAL]
    if requested is None:
        return fallback
    unique = list(dict.fromkeys(requested))
    picked = [seen[pid] for pid in unique if pid in seen]
    if unique and not picked:
        return fallback
    return picked[:MAX_EXTERNAL]


def label_external(papers: Iterable[ScholarPaper]) -> list[LabelledExternal]:
    return [
        LabelledExternal(
            ref=ExternalRef(
                label=f"P{i}",
                s2_id=p.s2_id,
                title=p.title,
                authors=p.authors,
                year=p.year,
                venue=p.venue,
                tldr=p.tldr,
                arxiv_id=p.arxiv_id,
                url=p.url,
            ),
            paper=p,
        )
        for i, p in enumerate(papers, start=1)
    ]


# ---------------------------------------------------------------- the writer's markers


def finalize_citations(text: str, valid: Iterable[str]) -> tuple[str, list[str]]:
    """Drop labels that are not in ``valid``, split grouped labels, and list what was cited.

    Returns the cleaned text and the labels it cites (S and P alike), in order of first use.
    """
    allowed = set(valid)
    cited: list[str] = []

    def replace(m: re.Match[str]) -> str:
        labels = [part.strip() for part in re.split(r"[,;]", m.group(2))]
        keep = [label for label in labels if label in allowed]
        if not keep:
            # Take the whitespace before the marker with it: "word [S9]." -> "word."
            return ""
        cited.extend(keep)
        return m.group(1) + "".join(f"[{label}]" for label in keep)

    cleaned = _CITATION.sub(replace, text)
    return cleaned, list(dict.fromkeys(cited))


def strip_citations(text: str) -> str:
    """Remove every [S#] and [P#] marker, for history read without the labels it referred to."""
    return _CITATION.sub("", text)


def citation_labels(text: str) -> list[str]:
    """Every label in ``text``, in order of appearance (repeats kept)."""
    return [label.strip() for m in _CITATION.finditer(text) for label in re.split(r"[,;]", m.group(2))]
