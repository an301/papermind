"""The one tokenizer for lexical retrieval, and the text each chunk is indexed as.

Queries and chunks must be tokenized identically or BM25 silently misses
(a chunk indexed as 'd_k' never matches a query tokenized as 'd', 'k'), so
both the index and the search path import ``tokenize`` from here.

Choices, each of which matters on ML papers:

* NFKC + lower case: ligatures and full-width forms fold ('ﬁne-tuning').
* Tokens are runs of letters/digits joined by '-', '_', '.' or an apostrophe,
  so notation survives whole: 'd_k', 'gpt-3', 'l2', 'bert-base', '28.4'.
* A compound also yields its parts of two or more characters: 'self-attention'
  indexes as 'self-attention', 'self', 'attention'. A reader types it either
  way, and the parts carry most of the meaning.
* A light plural fold (Harman's S-stemmer) so 'embeddings' matches
  'embedding'. Full stemming (Porter) conflates far more than that -- 'normal'
  and 'normalization', 'attend' and 'attention' -- which costs precision on
  exactly the technical vocabulary readers ask about. Acronym plurals
  ('GPUs', 'LLMs') fold to the acronym.
* No stopword list. Within one paper IDF already discounts 'the' and 'of',
  and a stopword list deletes real terms ('a' in 'Appendix A', 'it' in 'IT').
"""

from __future__ import annotations

import re
import unicodedata

from papermind.schemas.paper import Chunk

_TOKEN = re.compile(r"[^\W_]+(?:[-_.'’][^\W_]+)*")
_SPLIT = re.compile(r"[-_.'’]")
# 'GPUs', 'LLMs', 'RNNs': an upper-case acronym with a lower-case plural 's'.
# Detected before lower-casing, because afterwards 'gpus' looks like 'corpus'
# (which the S-stemmer rightly leaves alone) and would never match 'GPU'.
_ACRONYM_PLURAL = re.compile(r"[A-Z][A-Z0-9]+s")


# Plurals that add '-es' after a sibilant. Harman's rule maps every '-es' to
# '-e', which turns 'batches' into 'batche' and 'patches' into 'patche' -- so a
# query for 'batch' never matched 'mini-batches' in the BatchNorm paper, nor
# 'patch' the ViT paper's 'patches'. '-ses' is deliberately absent: 'responses',
# 'cases' and 'uses' are '-e' words, and they are far more common here than
# 'biases'.
_SIBILANT_ES = ("ches", "shes", "xes", "sses", "zzes")


def _fold_plural(token: str) -> str:
    """Harman's S-stemmer plus a sibilant-'-es' rule, alphabetic tokens only (never 'gpt-3s')."""
    if len(token) <= 3 or not token.isalpha():
        return token
    if token.endswith("ies") and not token.endswith(("eies", "aies")):
        return token[:-3] + "y"
    if token.endswith(_SIBILANT_ES):
        return token[:-2]
    if token.endswith("che"):
        # 'cache' -> 'cach', so it meets 'caches' (folded by the rule above).
        return token[:-1]
    if token.endswith("es") and not token.endswith(("aes", "ees", "oes")):
        return token[:-1]
    if token.endswith("s") and not token.endswith(("us", "ss")):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN.finditer(unicodedata.normalize("NFKC", text)):
        raw = match.group()
        if _ACRONYM_PLURAL.fullmatch(raw):
            tokens.append(raw[:-1].lower())
            continue
        token = raw.lower().replace("’", "'")
        tokens.append(_fold_plural(token))
        if _SPLIT.search(token):
            tokens.extend(_fold_plural(part) for part in _SPLIT.split(token) if len(part) >= 2)
    return tokens


def indexable_text(chunk: Chunk) -> str:
    """What both retrieval arms index: the section heading, then the chunk text.

    The heading is often the best evidence of what a chunk is about ('Hardware
    and Schedule' for a paragraph that never says 'compute'), and the chunk
    text alone does not contain it.
    """
    heading = " ".join(part for part in (chunk.section_number, chunk.section_title) if part)
    return f"{heading}\n{chunk.text}" if heading else chunk.text
