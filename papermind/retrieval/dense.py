"""The dense arm: sentence-transformers embeddings in a FAISS index.

Two decisions here are load-bearing.

**Windowing.** all-MiniLM-L6-v2 has a 256-token limit, and chunks run to 800 --
on the Transformer paper, 17 of 27 chunks exceed it. Encoding a chunk directly
would silently discard most of its text, and an ablation run on that would
report "dense retrieval is weak" when the real finding is "dense retrieval never
saw two thirds of the corpus". So each chunk is split into overlapping windows
that fit the encoder, every window is embedded, and a chunk's score is the *max*
over its windows. Max-pooling rather than mean because relevance is local: a
section that answers the query in one paragraph should not be diluted by the
five paragraphs that do not.

**IndexFlatIP.** Exact inner product over unit-normalised vectors is exact
cosine similarity. At a few hundred vectors per paper an approximate index
(IVF, HNSW) would trade recall for a speedup that is not needed -- a full scan
of 60 vectors is microseconds. Revisit only if a single paper starts producing
tens of thousands of windows.
"""

from __future__ import annotations

import logging

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

from papermind.ingest.models import Chunk
from papermind.retrieval.text import indexable_text

DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class _OverLengthTokenizerFilter(logging.Filter):
    """Drops transformers' "Token indices sequence length is longer" warning.

    Splitting a chunk into windows means tokenizing text that is deliberately
    longer than the model limit, and every tokenizer entry point emits this
    warning when that happens. Its advice ("will result in indexing errors")
    does not apply: the ids are sliced to window size before anything is
    encoded. Left in, it fires for most chunks of most papers and trains the
    reader to ignore tokenizer warnings -- including a real one.

    Filtered by message rather than by muting the logger, so everything else
    transformers has to say still gets through.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.getMessage().startswith("Token indices sequence length is longer")


def _silence_over_length_warning() -> None:
    logger = logging.getLogger("transformers.tokenization_utils_base")
    if not any(isinstance(f, _OverLengthTokenizerFilter) for f in logger.filters):
        logger.addFilter(_OverLengthTokenizerFilter())

# Leave room for the [CLS]/[SEP] tokens the encoder adds.
_WINDOW_MARGIN = 8
# Overlap between windows, so a passage straddling a boundary is whole in one
# of them.
_WINDOW_OVERLAP = 48


class EmbeddingModel:
    """Wraps the sentence-transformers model. One instance per process.

    The model is ~90MB and shared across every paper's index, so it is owned by
    the registry rather than by any single PaperIndex.
    """

    def __init__(self, name: str = DEFAULT_EMBEDDING_MODEL) -> None:
        self.name = name
        _silence_over_length_warning()
        self._model = SentenceTransformer(name)

        # Both are Optional on the SentenceTransformer API. A model that reports
        # neither cannot be indexed against, so fail here with the model name
        # rather than deep inside a FAISS call with a shape mismatch.
        dimension = self._model.get_embedding_dimension()
        max_tokens = self._model.max_seq_length
        if dimension is None or max_tokens is None:
            raise ValueError(
                f"{name} reports no embedding dimension or sequence limit; "
                "it cannot be used as a retrieval encoder"
            )
        self.dimension = int(dimension)
        self.max_tokens = int(max_tokens)

    def windows(self, text: str) -> list[str]:
        """Split text into encoder-sized overlapping windows.

        Splitting on the model's own tokenizer rather than on characters,
        because the whole point is to land just under a token limit.
        """
        tokenizer = self._model.tokenizer
        # Over-length input is the normal case here and the reason this function
        # exists; the resulting warning is filtered at construction. Every
        # tokenizer entry point emits it, so there is no quieter call to pick.
        ids = tokenizer.encode(text, add_special_tokens=False)
        size = self.max_tokens - _WINDOW_MARGIN
        if len(ids) <= size:
            return [text]

        step = size - _WINDOW_OVERLAP
        pieces: list[str] = []
        for start in range(0, len(ids), step):
            window = ids[start : start + size]
            if not window:
                break
            pieces.append(tokenizer.decode(window))
            if start + size >= len(ids):
                break
        return pieces

    def encode(self, texts: list[str]) -> np.ndarray:
        """Unit-normalised embeddings, float32, shape (len(texts), dimension)."""
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,   # makes inner product == cosine
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype=np.float32)


class DenseIndex:
    """FAISS index over the windows of one paper's chunks."""

    def __init__(self, chunks: list[Chunk], model: EmbeddingModel) -> None:
        self._model = model
        self._chunk_ids: list[str] = []      # one entry per window
        texts: list[str] = []

        for chunk in chunks:
            for window in model.windows(indexable_text(chunk)):
                texts.append(window)
                self._chunk_ids.append(chunk.id)

        self._index = faiss.IndexFlatIP(model.dimension)
        if texts:
            self._index.add(model.encode(texts))

    @property
    def n_windows(self) -> int:
        return self._index.ntotal

    def rank(self, query: str, limit: int) -> list[str]:
        """Chunk ids, best first, max-pooled over each chunk's windows."""
        if self._index.ntotal == 0:
            return []

        query_vector = self._model.encode([query])
        # Scan every window. Exact at this scale, and it guarantees a chunk is
        # never missed because its best window fell outside a truncated
        # window-level top-k.
        similarities, indices = self._index.search(query_vector, self._index.ntotal)

        best: dict[str, float] = {}
        for score, window_index in zip(similarities[0], indices[0], strict=True):
            if window_index < 0:            # FAISS pads short results with -1
                continue
            chunk_id = self._chunk_ids[window_index]
            if score > best.get(chunk_id, float("-inf")):
                best[chunk_id] = float(score)

        ordered = sorted(best.items(), key=lambda pair: (-pair[1], pair[0]))
        return [chunk_id for chunk_id, _ in ordered[:limit]]
