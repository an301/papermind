"""Classify what the agent typed into a Semantic Scholar paper reference.

The agent passes free text: a title, an arXiv id in any of its spellings, a DOI,
an S2 id, or a URL. Ids go straight to ``/paper/{id}`` (one request, exact);
everything else is a title and goes through the match/search endpoints. Getting
this wrong is expensive in both directions -- a title sent as an id is a 404, an
id sent as a title is a fuzzy search that can confidently return the wrong paper.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

RefKind = Literal["arxiv", "doi", "s2", "corpus", "url", "upload", "title"]

_ARXIV_NEW = r"\d{4}\.\d{4,5}"
# Pre-2007 ids: archive(.subject class)/YYMMNNN, e.g. hep-th/9901001, math.GT/0309136.
_ARXIV_OLD = r"[a-z]+(?:-[a-z]+)*(?:\.[a-z]{2})?/\d{7}"
_ARXIV_ID = rf"(?P<arxiv>{_ARXIV_NEW}|{_ARXIV_OLD})(?:v\d+)?"

_ARXIV_RE = re.compile(rf"^(?:arxiv:\s*)?{_ARXIV_ID}$", re.IGNORECASE)
_ARXIV_URL_RE = re.compile(
    rf"^(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html)/{_ARXIV_ID}(?:\.pdf)?/?$",
    re.IGNORECASE,
)
# arXiv mints DataCite DOIs (10.48550/arXiv.<id>) that S2 does not reliably index;
# the arXiv id inside them always resolves.
_ARXIV_DOI_RE = re.compile(rf"^10\.48550/arxiv\.{_ARXIV_ID}$", re.IGNORECASE)
_DOI_RE = re.compile(
    r"^(?:doi:\s*|(?:https?://)?(?:dx\.)?doi\.org/)?(?P<doi>10\.\d{4,9}/\S+)$", re.IGNORECASE
)
_S2_SHA_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_S2_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.|api\.)?semanticscholar\.org/paper/(?:[^/]+/)?(?P<sha>[0-9a-f]{40})/?$",
    re.IGNORECASE,
)
_CORPUS_RE = re.compile(r"^corpus(?:id)?:\s*(?P<corpus>\d+)$", re.IGNORECASE)
# The other hosts S2 documents for its URL: id form.
_S2_URL_HOSTS_RE = re.compile(
    r"^https?://(?:[a-z0-9-]+\.)*(?:aclweb\.org|acm\.org|biorxiv\.org)/\S+$",
    re.IGNORECASE,
)
# Uploaded PDFs get paper_id "up_<sha12>" at ingest. If one reaches us as the
# dispatcher's default_paper it is a local id, not something S2 can resolve.
_UPLOAD_RE = re.compile(r"^up_[0-9a-f]{12}$")

_QUOTES = "\"'`“”‘’«»"


@dataclass(frozen=True, slots=True)
class PaperRef:
    kind: RefKind
    value: str  # normalised id for id kinds, cleaned title text for titles

    @property
    def api_id(self) -> str | None:
        """The ``paper_id`` path segment S2 accepts, or None when this is not an id."""
        match self.kind:
            case "arxiv":
                return f"ARXIV:{self.value}"
            case "doi":
                return f"DOI:{self.value}"
            case "s2":
                return self.value
            case "corpus":
                return f"CorpusId:{self.value}"
            case "url":
                return f"URL:{self.value}"
            case _:
                return None


def classify(query: str) -> PaperRef:
    text = " ".join(query.split()).strip(_QUOTES + " ")

    if m := _ARXIV_RE.match(text) or _ARXIV_URL_RE.match(text):
        # Versions are dropped: S2 keys a paper by its arXiv id, and a pinned
        # version is noise for "what does this paper cite".
        return PaperRef("arxiv", m.group("arxiv"))
    if m := _DOI_RE.match(text):
        doi = m.group("doi").rstrip(".,;")
        if a := _ARXIV_DOI_RE.match(doi):
            return PaperRef("arxiv", a.group("arxiv"))
        return PaperRef("doi", doi)
    if _S2_SHA_RE.match(text):
        return PaperRef("s2", text.lower())
    if m := _S2_URL_RE.match(text):
        return PaperRef("s2", m.group("sha").lower())
    if m := _CORPUS_RE.match(text):
        return PaperRef("corpus", m.group("corpus"))
    if _S2_URL_HOSTS_RE.match(text):
        return PaperRef("url", text)
    if _UPLOAD_RE.match(text):
        return PaperRef("upload", text)
    return PaperRef("title", text)


def search_query(title: str) -> str:
    # /paper/search documents that hyphenated terms match nothing
    # ("pre-training" finds no BERT); spaces keep the tokens.
    return " ".join(title.replace("-", " ").split())


def normalise_title(title: str) -> str:
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())


def title_similarity(a: str, b: str) -> float:
    """0..1 similarity of two titles, ignoring case, punctuation, and accents."""
    na, nb = normalise_title(a), normalise_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()
