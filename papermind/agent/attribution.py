"""Which sentence of each cited passage the answer relied on (``SourceRef.cited_sentence``).

The passage card bolds this sentence and the page underlines it, so a wrong
pick is worse than none: when nothing in the passage clearly matches, the
field stays empty. It is computed after the answer is final, with no model
call, so it is reproducible and free:

1. Split the answer into sentences and collect, per label, the content words
   of every sentence that cites it. A marker written after the full stop
   ("... softmax. [S1]") belongs to the sentence before it.
2. Split each cited passage into sentences, inside paragraph boundaries.
3. Score each passage sentence by the content words it shares with the
   answer, each weighted by how rare it is among that passage's sentences (a
   word every sentence contains says nothing about which one was used), over
   a gentle length penalty so a long sentence does not win on bulk alone.
4. Keep the best one if it shares at least ``MIN_SHARED`` words.

The sentence is returned whitespace-collapsed, exactly as it appears in
``" ".join(chunk_text.split())``, so the UI can find it in the passage text.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence

from papermind.agent.labels import LabelledSource, citation_labels, strip_citations
from papermind.schemas.events import SourceRef

MIN_SHARED = 2
LENGTH_EXPONENT = 0.25

# Abbreviations whose full stop does not end a sentence in ML papers ("al." is "et al.").
_ABBREVIATIONS = frozenset(
    "e.g. i.e. al. fig. figs. eq. eqs. sec. secs. tab. vs. cf. no. approx. resp. ref. refs. appx. ch. viz.".split()
)
# A sentence ends at . ! or ?, optionally followed by closing quotes or brackets, then whitespace.
_BOUNDARY = re.compile(r"[.!?][\"'”’)\]]*\s+")
_LEADING_MARKERS = re.compile(r"^(?:\s*\[[SP]\d+(?:\s*[,;]\s*[SP]\d+)*\])+")
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[a-z][a-z0-9]*")
_STOPWORDS = frozenset(
    """a about above after again against all also an and any are as at be because been before being
    below between both but by can could did do does doing done down during each few for from further
    had has have having he her here hers him his how if in into is it its itself just more most much
    must no nor not now of off on once only or other our ours out over own same she should so some
    such than that the their theirs them then there these they this those through to too under until
    up upon very via was we were what when where which while who whom why will with would you your
    yours one two used use using uses thus hence however therefore instead rather""".split()
)


def split_sentences(text: str) -> list[str]:
    """Sentences of ``text``, never across a paragraph or line break, whitespace-collapsed."""
    sentences: list[str] = []
    for block in re.split(r"\n\s*\n|\n(?=\s*(?:[-*+]|\d+[.)])\s)", text):
        sentences += _split_block(" ".join(block.split()))
    return sentences


def _split_block(block: str) -> list[str]:
    out: list[str] = []
    start = 0
    for m in _BOUNDARY.finditer(block):
        stop = block[m.start()]
        before = block[start : m.start() + 1]
        last_word = before.rsplit(" ", 1)[-1].lower()
        following = block[m.end() : m.end() + 1]
        if stop == "." and (last_word in _ABBREVIATIONS or re.fullmatch(r"[a-z]\.", last_word)):
            continue  # "et al. show", "A. Vaswani"
        if re.fullmatch(r"\d+\.", before.strip()):
            continue  # a numbered list item's marker: "1. Numbered."
        if following.islower():
            continue  # a full stop mid-sentence: "approx. the", "vs. dense"
        out.append(block[start : m.end()].strip())
        start = m.end()
    if tail := block[start:].strip():
        out.append(tail)
    return out


def _stem(word: str) -> str:
    """A deliberately crude suffix strip: 'scaled', 'scaling' and 'scale' all become 'scal'."""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s", "ly"):
        if word.endswith(suffix) and not word.endswith("ss") and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def content_words(text: str) -> set[str]:
    """Lowercased, stemmed, stopword-free words. TeX is flattened so '$\\sqrt{d_k}$' meets '√d_k'."""
    flat = re.sub(r"\\(?=[a-z])|[{}]", " ", strip_citations(text).lower())
    flat = re.sub(r"[_^]", "", flat)
    return {_stem(t) for t in _TOKEN.findall(flat) if t not in _STOPWORDS and (len(t) > 1 or t.isdigit())}


def words_by_label(answer: str) -> dict[str, set[str]]:
    """Per citation label, the content words of the answer sentences that carry it."""
    by_label: dict[str, set[str]] = defaultdict(set)
    sentences = split_sentences(answer)
    # Markers that open a sentence close the one before it: "... softmax. [S1] Next ..."
    moved: list[tuple[str, list[str]]] = []
    for sentence in sentences:
        lead = _LEADING_MARKERS.match(sentence)
        if lead and moved:
            moved[-1][1].extend(citation_labels(lead.group(0)))
            sentence = sentence[lead.end() :]
        moved.append((sentence, citation_labels(sentence)))
    for sentence, labels in moved:
        if not labels:
            continue
        words = content_words(sentence)
        for label in labels:
            by_label[label] |= words
    return dict(by_label)


def best_sentence(passage: str, answer_words: set[str]) -> str | None:
    """The passage sentence sharing the most (rarity-weighted) words with ``answer_words``."""
    sentences = split_sentences(passage)
    if not sentences or not answer_words:
        return None
    bags = [content_words(s) for s in sentences]
    df = Counter(word for bag in bags for word in bag)
    n = len(sentences)
    best: tuple[float, int] | None = None
    for i, bag in enumerate(bags):
        shared = bag & answer_words
        if len(shared) < MIN_SHARED:
            continue
        weight = sum(1.0 + math.log(n / df[w]) for w in shared)
        score = weight / len(bag) ** LENGTH_EXPONENT
        # Strictly greater: on a tie the earlier sentence wins, which keeps the pick stable.
        if best is None or score > best[0]:
            best = (score, i)
    return sentences[best[1]] if best is not None else None


def cited_sentences(answer: str, sources: Iterable[LabelledSource]) -> dict[str, str]:
    """label -> the sentence of that passage the answer relied on most, for cited labels only."""
    by_label = words_by_label(answer)
    out: dict[str, str] = {}
    for source in sources:
        words = by_label.get(source.label)
        if words and (sentence := best_sentence(source.text, words)) is not None:
            out[source.label] = sentence
    return out


def with_cited_sentences(answer: str, sources: Sequence[LabelledSource]) -> list[SourceRef]:
    """The source refs, each cited one carrying its ``cited_sentence``; labels and order unchanged."""
    picked = cited_sentences(answer, sources)
    return [
        s.ref.model_copy(update={"cited_sentence": picked[s.label]}) if s.label in picked else s.ref
        for s in sources
    ]
