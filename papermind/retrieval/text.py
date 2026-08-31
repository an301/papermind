"""What actually goes into an index, shared by both retrieval arms.

Both arms index the *same* string. That is a correctness requirement for the
ablation, not a convenience: if BM25 saw the section title and the dense arm did
not, a measured difference between them would partly be a difference in input,
and the comparison would mean nothing.
"""

from __future__ import annotations

import re

from papermind.ingest.models import Chunk

# Keep single-character tokens. In an ML paper 'Q', 'K', 'V', 'd' and 'n' are
# the names of the things being asked about, not noise -- dropping them would
# make the query 'what are Q K and V' unanswerable by the lexical arm.
_TOKEN = re.compile(r"[a-z0-9]+")


def indexable_text(chunk: Chunk) -> str:
    """The chunk's text, prefixed with its section title.

    The title is strong topical signal that the body often omits -- section 3.2
    is titled 'Attention' and then never says the bare word again for two
    paragraphs. Prefixing helps both arms, and keeps them comparable.
    """
    return f"{chunk.section_title}\n\n{chunk.text}"


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens.

    No stemming and no stopword list, both deliberate:

    - Stemming would need another dependency and mostly helps with morphology
      that ML papers do not have much of; the terms that matter ('softmax',
      'BLEU', 'LoRA') are already invariant. It is a reasonable ablation arm to
      add later, which is another reason to keep it out of the baseline.
    - Stopwords are handled by BM25's IDF term on their own. With per-paper
      indexes a word appearing in every chunk gets near-zero IDF, which is
      exactly the intended effect.

    Hyphenated compounds split ('self-attention' -> 'self', 'attention') so a
    query for 'attention' still matches.
    """
    return _TOKEN.findall(text.lower())
