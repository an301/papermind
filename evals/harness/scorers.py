"""Scoring functions. Pure: no I/O, no model calls, deterministic.

Everything checkable without a judge is checked here: retrieval against the
gold chunk id (exact match -- the label is the chunk the question was written
from), token overlap with the reference answer, latency and cost summaries,
calibration accuracy from judge labels, and agreement between a judge and a
human. The judges themselves live in ``judges.py``.
"""

from __future__ import annotations

import math
import re
import string
from collections import Counter
from collections.abc import Hashable, Iterable, Mapping, Sequence
from typing import Any, TypeVar

T = TypeVar("T", bound=Hashable)

LEVEL_ORDER: tuple[str, ...] = ("beginner", "intermediate", "expert")

# ---------------------------------------------------------------- retrieval


def gold_rank(gold: str, retrieved_ids: Sequence[str]) -> int | None:
    """1-based rank of the gold chunk in ``retrieved_ids``, or None when absent."""
    for rank, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id == gold:
            return rank
    return None


def recall_at_k(gold: str, retrieved_ids: Sequence[str], k: int) -> float:
    """1.0 when the gold chunk is among the first ``k`` ids, else 0.0.

    One gold chunk per question, so recall@k is a hit rate; averaged over the
    benchmark it is the fraction of questions whose answering passage was
    retrieved. Exact id match, no judge: the label is the chunk the question
    was generated from.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    return 1.0 if gold in retrieved_ids[:k] else 0.0


def reciprocal_rank(gold: str, retrieved_ids: Sequence[str], k: int | None = None) -> float:
    """1/rank of the gold chunk within the first ``k`` ids (all when None), 0.0 when absent."""
    rank = gold_rank(gold, retrieved_ids if k is None else retrieved_ids[:k])
    return 0.0 if rank is None else 1.0 / rank


def mean(values: Iterable[float | None]) -> float | None:
    """Mean of the non-None values; None when there are none (not 0.0, which would read as a score)."""
    present = [float(v) for v in values if v is not None]
    return sum(present) / len(present) if present else None


def ratio(numerator: float, denominator: float) -> float | None:
    return None if denominator == 0 else numerator / denominator


# ---------------------------------------------------------------- answer text

# [S1], [S1][S3], and the grouped forms the citation finaliser normally splits.
CITATION_MARKER = re.compile(r"\[(?:S|P)\d+(?:\s*[,;]\s*(?:S|P)\d+)*\]")
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH = re.compile(r"(?<!\\)\$(.+?)(?<!\\)\$", re.DOTALL)
_PAREN_MATH = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]", re.DOTALL)
_LATEX_COMMAND = re.compile(r"\\([A-Za-z]+)")
# Layout commands carry no content; any other command name is a symbol the
# reference may spell out in words ("beta", "sqrt" is not one, "softmax" is).
_LATEX_LAYOUT = frozenset(
    {
        "frac", "dfrac", "tfrac", "sqrt", "left", "right", "big", "bigg", "Big", "Bigg", "cdot", "times",
        "mathrm", "mathbf", "mathit", "mathcal", "mathbb", "boldsymbol", "operatorname", "text", "textbf",
        "textit", "quad", "qquad", "top", "intercal", "ldots", "cdots", "dots", "hat", "tilde", "bar",
        "vec", "begin", "end", "limits", "displaystyle", "mid", "le", "leq", "ge", "geq", "in",
    }
)  # fmt: skip
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
_MD_QUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`+|~~)")
_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCTUATION = str.maketrans("", "", string.punctuation)


# Inside math, grouping and operators separate symbols ("softmax(QK)" is two
# words); sub/superscript marks do not ("d_k" stays one token, as in prose).
_MATH_SEPARATORS = re.compile(r"[{}()\[\]+\-*/=<>|,;:!&]")


def _unwrap_math(match: re.Match[str]) -> str:
    body = next(g for g in match.groups() if g is not None)
    body = _LATEX_COMMAND.sub(lambda m: " " if m.group(1) in _LATEX_LAYOUT else f" {m.group(1)} ", body)
    return f" {_MATH_SEPARATORS.sub(' ', body)} "


def strip_markup(text: str) -> str:
    """Answer text without citation markers, markdown or math delimiters.

    Math is unwrapped rather than deleted: an expert answer writes
    $\\sqrt{d_k}$ where the reference says "the square root of d_k", and
    deleting the math would drop exactly the tokens the two share. Layout
    commands go; symbol names (beta, softmax, log) stay as words.
    """
    text = CITATION_MARKER.sub(" ", text)
    text = _DISPLAY_MATH.sub(_unwrap_math, text)
    text = _PAREN_MATH.sub(_unwrap_math, text)
    text = _INLINE_MATH.sub(_unwrap_math, text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_LIST.sub("", text)
    text = _MD_QUOTE.sub("", text)
    text = _MD_EMPHASIS.sub("", text)
    return text


def normalize_answer(text: str) -> str:
    """SQuAD normalisation (lowercase, no punctuation, no articles, single spaces) after ``strip_markup``."""
    text = strip_markup(text).lower()
    text = text.translate(_PUNCTUATION)
    text = _ARTICLES.sub(" ", text)
    return " ".join(text.split())


def token_f1(prediction: str, reference: str) -> float:
    """SQuAD-style token F1 between an answer and the reference answer.

    A recall-heavy signal in practice: references are 2-4 sentences and
    answers are paragraphs, so precision is low by construction and a longer
    answer scores lower. Useful for regressions run over run, not as an
    absolute quality number.
    """
    pred = normalize_answer(prediction).split()
    ref = normalize_answer(reference).split()
    if not pred or not ref:
        # Both empty is agreement; one empty is a miss.
        return float(pred == ref)
    common = Counter(pred) & Counter(ref)
    overlap = sum(common.values())
    if overlap == 0:
        return 0.0
    precision = overlap / len(pred)
    recall = overlap / len(ref)
    return 2 * precision * recall / (precision + recall)


# ---------------------------------------------------------------- latency and cost


def percentile(values: Sequence[float], p: float) -> float:
    """The ``p``-th percentile (0-100) with linear interpolation between ranks (numpy's default)."""
    if not values:
        raise ValueError("percentile of no values")
    if not 0 <= p <= 100:
        raise ValueError("p must be within [0, 100]")
    ordered = sorted(float(v) for v in values)
    position = (len(ordered) - 1) * p / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def latency_summary(values_ms: Iterable[float | None]) -> dict[str, float | int | None]:
    """p50 / p95 / max in milliseconds.

    Never the mean: latency is long-tailed (a retried tool, a slow writer), and
    a mean hides the slow turns a reader actually notices inside an average
    that matches no real turn.
    """
    present = [float(v) for v in values_ms if v is not None]
    if not present:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    return {
        "n": len(present),
        "p50": round(percentile(present, 50), 1),
        "p95": round(percentile(present, 95), 1),
        "max": round(max(present), 1),
    }


def cost_summary(costs_usd: Iterable[float | None]) -> dict[str, float | int | None]:
    """Total and per-query cost. Here the mean is the right statistic: costs add up to the bill."""
    present = [float(c) for c in costs_usd if c is not None]
    if not present:
        return {"n": 0, "total_usd": 0.0, "mean_usd": None, "p50_usd": None, "p95_usd": None, "max_usd": None}
    return {
        "n": len(present),
        "total_usd": round(sum(present), 4),
        "mean_usd": round(sum(present) / len(present), 5),
        "p50_usd": round(percentile(present, 50), 5),
        "p95_usd": round(percentile(present, 95), 5),
        "max_usd": round(max(present), 5),
    }


def cache_summary(usages: Iterable[Mapping[str, Any]]) -> dict[str, float | int | None]:
    """Prompt-cache totals across turns. ``input_tokens`` is only the uncached remainder of each prompt."""
    read = write = uncached = 0
    for u in usages:
        read += int(u.get("cache_read_input_tokens", 0) or 0)
        write += int(u.get("cache_creation_input_tokens", 0) or 0)
        uncached += int(u.get("input_tokens", 0) or 0)
    prompt = read + write + uncached
    return {
        "prompt_tokens": prompt,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": write,
        "uncached_input_tokens": uncached,
        "cache_hit_rate": None if prompt == 0 else round(read / prompt, 4),
    }


# ---------------------------------------------------------------- calibration


def _pairs(labels: Sequence[T | None], targets: Sequence[T | None]) -> list[tuple[T, T]]:
    if len(labels) != len(targets):
        raise ValueError("labels and targets must be the same length")
    # A missing label (judge failed) is excluded, not counted wrong: it says
    # nothing about the answer.
    return [(a, b) for a, b in zip(labels, targets, strict=True) if a is not None and b is not None]


def calibration_accuracy(labels: Sequence[str | None], targets: Sequence[str | None]) -> float | None:
    """Fraction of answers the blind judge placed at the level they were written for."""
    pairs = _pairs(labels, targets)
    return None if not pairs else sum(a == b for a, b in pairs) / len(pairs)


def confusion_matrix(
    labels: Sequence[str | None], targets: Sequence[str | None], classes: Sequence[str] = LEVEL_ORDER
) -> dict[str, dict[str, int]]:
    """``matrix[target][label]``: rows are the level asked for, columns the level the judge saw."""
    matrix = {t: dict.fromkeys(classes, 0) for t in classes}
    for label, target in _pairs(labels, targets):
        matrix.setdefault(target, dict.fromkeys(classes, 0))
        matrix[target][label] = matrix[target].get(label, 0) + 1
    return matrix


def per_level_accuracy(
    labels: Sequence[str | None], targets: Sequence[str | None], classes: Sequence[str] = LEVEL_ORDER
) -> dict[str, dict[str, float | int | None]]:
    """Accuracy sliced by requested level.

    Always report this next to the aggregate: a writer that nails expert
    answers and patronises beginners can score fine overall and still fail the
    readers the product is for.
    """
    out: dict[str, dict[str, float | int | None]] = {}
    pairs = _pairs(labels, targets)
    for level in classes:
        mine = [(a, b) for a, b in pairs if b == level]
        out[level] = {
            "n": len(mine),
            "accuracy": None if not mine else sum(a == b for a, b in mine) / len(mine),
        }
    return out


def cohen_kappa(
    a: Sequence[T], b: Sequence[T], *, weights: str | None = None, classes: Sequence[T] | None = None
) -> float | None:
    """Cohen's kappa between two raters; ``weights="linear"`` for ordinal labels.

    Kappa corrects raw agreement for the agreement two raters would reach by
    chance given their label frequencies: a judge that says "intermediate"
    every time agrees with a human on a third of a balanced set and has kappa
    0. Linear weights count beginner-vs-expert as twice the disagreement of
    beginner-vs-intermediate, which is what the level scale means.
    """
    if len(a) != len(b):
        raise ValueError("raters must label the same items")
    if not a:
        return None
    labels: list[T] = list(classes) if classes is not None else sorted(set(a) | set(b), key=str)
    index = {label: i for i, label in enumerate(labels)}
    size = len(labels)
    n = len(a)
    observed = [[0.0] * size for _ in range(size)]
    for x, y in zip(a, b, strict=True):
        observed[index[x]][index[y]] += 1 / n
    row = [sum(observed[i]) for i in range(size)]
    col = [sum(observed[i][j] for i in range(size)) for j in range(size)]

    def weight(i: int, j: int) -> float:
        if weights is None:
            return 0.0 if i == j else 1.0
        if weights == "linear":
            return abs(i - j) / (size - 1) if size > 1 else 0.0
        raise ValueError(f"unknown weights {weights!r}")

    disagreement = sum(weight(i, j) * observed[i][j] for i in range(size) for j in range(size))
    chance = sum(weight(i, j) * row[i] * col[j] for i in range(size) for j in range(size))
    if chance == 0:
        # Both raters used one identical label throughout: agreement is
        # perfect but carries no information beyond chance.
        return 1.0 if disagreement == 0 else 0.0
    return 1 - disagreement / chance


def judge_agreement(human: Sequence[str | None], judge: Sequence[str | None]) -> dict[str, Any]:
    """How far to trust the calibration judge: raw agreement, Cohen's kappa, and the confusion matrix."""
    pairs = _pairs(human, judge)
    hs = [h for h, _ in pairs]
    js = [j for _, j in pairs]
    classes = [c for c in LEVEL_ORDER if c in set(hs) | set(js)] + sorted(
        (set(hs) | set(js)) - set(LEVEL_ORDER)
    )
    return {
        "n": len(pairs),
        "agreement": None if not pairs else sum(h == j for h, j in pairs) / len(pairs),
        "kappa": cohen_kappa(hs, js, classes=classes),
        "kappa_linear": cohen_kappa(hs, js, weights="linear", classes=classes),
        # Rows: the human's label; columns: the judge's.
        "confusion": confusion_matrix(js, hs, classes=classes or LEVEL_ORDER),
    }


# ---------------------------------------------------------------- citation gates

# docs/design/DESIGN.md Appendix A13: the bracket and the Background label ship
# only while both hold on the benchmark.
CITATION_PRECISION_GATE = 0.95
UNCITED_RATE_GATE = 0.05


def citation_precision(supported: Iterable[bool]) -> float | None:
    """Fraction of [S#] markers whose sentence the cited chunk supports. Design gate: >= 0.95."""
    verdicts = list(supported)
    return ratio(sum(verdicts), len(verdicts))


def uncited_rate(paragraphs: Iterable[tuple[bool, bool]]) -> float | None:
    """Of the paragraphs that state things from the paper, the fraction with no [S#]. Design gate: < 0.05.

    Takes (paper_derived, cited) per paragraph. Paragraphs of general
    background are left out of both sides: they need no citation.
    """
    derived = [cited for paper_derived, cited in paragraphs if paper_derived]
    return ratio(sum(not c for c in derived), len(derived))


def slice_by(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, list[Mapping[str, Any]]]:
    """Rows grouped by ``row[key]``, levels in their natural order, everything else sorted."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key)), []).append(row)
    ordered = [k for k in LEVEL_ORDER if k in groups] + sorted(k for k in groups if k not in LEVEL_ORDER)
    return {k: groups[k] for k in ordered}
