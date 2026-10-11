"""Concept-name normalisation and free-text resolution to concept ids.

Two different strings reach the graph: names the model writes ("Self Attention",
"batchnorm", "LLMs") and ids the API passes ("self-attention"). Both go through
``normalize_concept`` so that one dictionary lookup answers the common case and
fuzzy matching only has to deal with real typos.

The normalised key is internal. It never needs to be a correct English word,
it only needs singular and plural, hyphenated and spaced, British and American
spellings of the *same* concept to collide -- and distinct concepts not to.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import NamedTuple

from rapidfuzz import fuzz, process

from papermind.schemas.tools import ConceptRef

# Words that end in "s" but are not plurals. Generic suffix rules below already
# protect -ss (loss), -us (corpus) and -is (analysis); these are the rest.
_INVARIANT = frozenset(
    {
        "alias",
        "atlas",
        "bayes",
        "bias",
        "canvas",
        "chaos",
        "cosmos",
        "ethos",
        "gas",
        "kudos",
        "lens",
        "news",
        "pandas",
        "series",
        "species",
    }
)

# Plurals no suffix rule gets right. Folding them to the singular keeps
# "local minima" and "local minimum" on the same key.
_IRREGULAR = {
    "aliases": "alias",
    "analyses": "analysis",
    "axes": "axis",
    "biases": "bias",
    "caches": "cache",
    "corpora": "corpus",
    "cpus": "cpu",
    "criteria": "criterion",
    "diagnoses": "diagnosis",
    "gases": "gas",
    "gpus": "gpu",
    "halves": "half",
    "indices": "index",
    "leaves": "leaf",
    "lenses": "lens",
    "matrices": "matrix",
    "maxima": "maximum",
    "minima": "minimum",
    "niches": "niche",
    "optima": "optimum",
    "phenomena": "phenomenon",
    "spectra": "spectrum",
    "tpus": "tpu",
    "vertices": "vertex",
}

_TOKEN_RE = re.compile(r"[^\W_]+")
_POSSESSIVE_RE = re.compile(r"(?<=\w)['’]s\b")


def _strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _is_acronym_plural(token: str) -> bool:
    # "GPUs", "LLMs", "ReLUs": an upper-case stem with a lower-case "s". Must be
    # decided before casefolding, because "gpus" on its own looks like "corpus"
    # and the -us rule would (correctly, for words) leave it alone.
    stem = token[:-1]
    return (
        len(token) >= 3
        and token.endswith("s")
        and sum(ch.isupper() for ch in stem) >= 2
        and (stem[-1].isupper() or stem[-1].isdigit())
    )


def _singular(token: str) -> str:
    if token in _INVARIANT:
        return token
    if token in _IRREGULAR:
        return _IRREGULAR[token]
    if len(token) <= 3 or not token.endswith("s") or any(ch.isdigit() for ch in token):
        return token
    if token.endswith(("ss", "us", "is")):  # loss, corpus, analysis
        return token
    if token.endswith("ies") and len(token) > 4:  # probabilities -> probability
        return token[:-3] + "y"
    if token.endswith(("yses", "eses")):  # analyses, hypotheses -> -sis
        return token[:-2] + "is"
    if token.endswith(("sses", "xes", "zzes", "ches", "shes")):  # losses, softmaxes, batches
        return token[:-2]
    return token[:-1]


def _american(token: str) -> str:
    # Only the suffixes that show up in concept names (normalisation,
    # optimiser). A blanket -ise -> -ize would be harmless for matching but
    # turns ordinary words into noise in debug output for no gain.
    if token.endswith("isation"):
        return token[: -len("isation")] + "ization"
    if token.endswith("iser") and len(token) > 6:
        return token[: -len("iser")] + "izer"
    return token


def normalize_concept(name: str) -> str:
    """Canonical lookup key: casefolded, punctuation-free, singular, single-spaced.

    ``"Self-Attention"``, ``"self_attention"`` and ``"self attention"`` all become
    ``"self attention"``; ``"Transformers"`` becomes ``"transformer"`` while
    ``"loss"``, ``"bias"`` and ``"analysis"`` are left alone.
    """
    text = unicodedata.normalize("NFKC", name)
    text = _POSSESSIVE_RE.sub("", text)
    keys: list[str] = []
    for raw in _TOKEN_RE.findall(text):
        if _is_acronym_plural(raw):
            keys.append(_strip_diacritics(raw[:-1]).casefold())
            continue
        token = _strip_diacritics(raw).casefold()
        keys.append(_american(_singular(token)))
    return " ".join(keys)


def compact_key(normalized: str) -> str:
    """Space-insensitive form, so "batch norm" can meet the alias "batchnorm"."""
    return normalized.replace(" ", "")


# ---------------------------------------------------------------- resolution


@dataclass(frozen=True)
class IndexEntry:
    concept_id: str
    name: str
    aliases: tuple[str, ...] = ()


class Resolution(NamedTuple):
    """``(ref, score, suggestions)``; unpacks like the tuple ``GraphStore.resolve`` returns."""

    ref: ConceptRef | None
    score: float
    suggestions: list[ConceptRef]


class ConceptIndex:
    """Resolves free text to a concept over ids, names and aliases.

    Matching runs in tiers and the first tier that answers wins:

    1. exact normalised key (score 1.0);
    2. exact space-insensitive key, "batch norm" vs "batchnorm" (score
       ``COMPACT_SCORE``);
    3. fuzzy: ``token_sort_ratio`` against every key. Accepted only when the
       best concept scores at least ``FUZZY_ACCEPT`` and beats the runner-up by
       ``FUZZY_MARGIN``, and only for keys of ``MIN_FUZZY_LEN`` characters or
       more.

    The fuzzy thresholds are deliberately tight. The cost of a wrong match is a
    confidently wrong prerequisite analysis; the cost of no match is a list of
    suggestions the agent can retry with. At 90, single typos in real words
    pass ("softmx" 92, "batch normalisation" vs "-ization" 95) while sibling
    concepts do not ("relu"/"gelu" 75, "adam"/"adamw" 89, "elu"/"relu" 86).
    ``token_sort_ratio`` rather than ``WRatio`` for acceptance because WRatio
    rewards partial matches, which would accept "attention" as
    "self attention". WRatio *is* the right scorer for suggestions, where
    recall matters more than precision; ``SUGGEST_MIN`` = 70 keeps related
    names ("multi head attention" -> "self attention" 74) and drops letter-soup
    overlaps ("attention" -> "batch normalization" 64).
    """

    COMPACT_SCORE = 0.98
    FUZZY_ACCEPT = 90.0
    FUZZY_MARGIN = 3.0
    MIN_FUZZY_LEN = 4
    SUGGEST_MIN = 70.0

    def __init__(self, entries: Iterable[IndexEntry] = ()) -> None:
        self.entries: dict[str, IndexEntry] = {}
        # (key, kept concept_id, rejected concept_id) for keys two concepts share.
        self.collisions: list[tuple[str, str, str]] = []
        self._exact: dict[str, str] = {}
        self._compact: dict[str, str] = {}
        # Parallel lists: rapidfuzz scores ``_choices`` and returns positions.
        self._choices: list[str] = []
        self._owners: list[str] = []
        for entry in entries:
            self.add(entry)

    def add(self, entry: IndexEntry) -> None:
        self.entries[entry.concept_id] = entry
        for text in (entry.concept_id, entry.name, *entry.aliases):
            key = normalize_concept(text)
            if not key:
                continue
            owner = self._exact.get(key)
            if owner == entry.concept_id:
                continue
            if owner is not None:
                # Validation rejects these in the seed data; at runtime the
                # first concept keeps the key so resolution stays deterministic.
                self.collisions.append((key, owner, entry.concept_id))
                continue
            self._exact[key] = entry.concept_id
            self._compact.setdefault(compact_key(key), entry.concept_id)
            self._choices.append(key)
            self._owners.append(entry.concept_id)

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, concept_id: object) -> bool:
        return concept_id in self.entries

    def ref(self, concept_id: str) -> ConceptRef | None:
        entry = self.entries.get(concept_id)
        return ConceptRef(concept_id=entry.concept_id, name=entry.name) if entry else None

    def _ref(self, concept_id: str) -> ConceptRef:
        entry = self.entries[concept_id]
        return ConceptRef(concept_id=entry.concept_id, name=entry.name)

    def _best_per_concept(
        self, query: str, scorer: Callable[..., float], limit: int
    ) -> list[tuple[str, float]]:
        best: dict[str, float] = {}
        for _choice, score, idx in process.extract(query, self._choices, scorer=scorer, limit=limit):
            cid = self._owners[idx]
            if score > best.get(cid, -1.0):
                best[cid] = float(score)
        return sorted(best.items(), key=lambda item: (-item[1], self.entries[item[0]].name))

    def resolve(self, query: str, *, max_suggestions: int = 5) -> Resolution:
        key = normalize_concept(query)
        if not key or not self._choices:
            return Resolution(None, 0.0, [])
        if key in self._exact:
            return Resolution(self._ref(self._exact[key]), 1.0, [])
        compact = compact_key(key)
        if compact in self._compact:
            return Resolution(self._ref(self._compact[compact]), self.COMPACT_SCORE, [])

        ranked = self._best_per_concept(key, fuzz.token_sort_ratio, limit=20)
        if ranked and len(key) >= self.MIN_FUZZY_LEN:
            best_id, best = ranked[0]
            runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
            if best >= self.FUZZY_ACCEPT and best - runner_up >= self.FUZZY_MARGIN:
                return Resolution(self._ref(best_id), round(best / 100, 4), [])

        suggestions = [
            (cid, score)
            for cid, score in self._best_per_concept(key, fuzz.WRatio, limit=40)
            if score >= self.SUGGEST_MIN
        ][:max_suggestions]
        top = ranked[0][1] / 100 if ranked else 0.0
        return Resolution(None, round(top, 4), [self._ref(cid) for cid, _ in suggestions])

    def search(self, query: str, limit: int = 20) -> list[tuple[ConceptRef, float]]:
        """Ranked matches for a search box: exact, then prefix, then substring, then fuzzy."""
        key = normalize_concept(query)
        if not key:
            ordered = sorted(self.entries.values(), key=lambda e: e.name.casefold())
            return [(self._ref(e.concept_id), 0.0) for e in ordered[:limit]]
        best: dict[str, float] = {}
        for choice, cid in zip(self._choices, self._owners, strict=True):
            if choice == key:
                score = 100.0
            elif choice.startswith(key):
                score = 95.0
            elif f" {key}" in f" {choice}":
                score = 90.0
            else:
                continue
            best[cid] = max(best.get(cid, 0.0), score)
        for cid, score in self._best_per_concept(key, fuzz.WRatio, limit=max(limit * 3, 30)):
            if score >= self.SUGGEST_MIN and cid not in best:
                # Fuzzy hits rank below every literal hit.
                best[cid] = min(score, 89.0)
        ranked = sorted(best.items(), key=lambda item: (-item[1], self.entries[item[0]].name.casefold()))
        return [(self._ref(cid), round(score / 100, 4)) for cid, score in ranked[:limit]]
