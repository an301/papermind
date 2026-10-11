"""The dense arm: chunk embeddings (fastembed, BAAI/bge-base-en-v1.5) in a FAISS index.

Decisions that matter:

**Windows, max-pooled.** The encoder reads at most 512 tokens and silently
truncates the rest. Chunks are budgeted at ~600 estimated tokens and the
heading is prepended, so many chunks are longer than the encoder window;
embedding them whole would mean dense retrieval never sees their second half
and an ablation would blame the method instead of the truncation. Each chunk is
cut into overlapping, word-aligned windows by the model's own tokenizer, every
window is embedded, and a chunk scores as the *max* over its windows: relevance
is local, and one answering paragraph should not be diluted by the rest.

**Query instruction.** bge models are trained with an instruction prefix on
queries (not passages) for retrieval. fastembed does not add it; we do.

**IndexFlatIP over unit vectors** is exact cosine similarity. A paper has a
few hundred windows at most, so an approximate index would trade recall for a
speed-up nobody needs.

**One model per process, computed once per paper.** The model is ~200 MB and
embedding is ~7 windows/s on a laptop CPU, so it is loaded once (``get_embedder``)
and window vectors are cached on disk, content-addressed by model, window
settings and chunk texts (``EmbeddingCache``). Ingestion fills the cache in its
INDEXING stage, so the retriever process only loads vectors. Inference runs in
a worker thread under a lock: ONNX Runtime already uses every core per call.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import threading
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import faiss
import numpy as np

from papermind.config import get_settings
from papermind.ingest.store import write_atomic

if TYPE_CHECKING:
    from tokenizers import Tokenizer

log = logging.getLogger(__name__)

BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
# 512 positions minus [CLS] and [SEP], minus slack: a window's text is
# re-tokenized when embedded and must not spill past the model limit.
WINDOW_TOKENS = 480
# A passage that straddles a window boundary is whole in one of the two.
WINDOW_OVERLAP = 64
EMBED_BATCH = 16

ProgressFn = Callable[[float], Awaitable[None]]


class Embedder(Protocol):
    """What the dense arm needs from an embedding model. Methods block; call them in a thread."""

    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    def windows(self, text: str) -> list[str]:
        """``text`` cut into pieces the encoder can read whole."""
        ...

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        """(len(texts), dim) float32, unit rows."""
        ...

    def embed_query(self, text: str) -> np.ndarray:
        """(dim,) float32, unit."""
        ...


def _unit_rows(vectors: Any) -> np.ndarray:
    array = np.ascontiguousarray(np.asarray(vectors, dtype=np.float32))
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.size:
        faiss.normalize_L2(array)
    return array


class FastEmbedEmbedder:
    """bge-base-en-v1.5 via fastembed (ONNX Runtime; no torch, which clashes with faiss's OpenMP)."""

    def __init__(
        self,
        model_name: str,
        cache_dir: Path,
        *,
        window_tokens: int = WINDOW_TOKENS,
        overlap_tokens: int = WINDOW_OVERLAP,
        query_instruction: str = BGE_QUERY_INSTRUCTION,
    ) -> None:
        if not 0 <= overlap_tokens < window_tokens:
            raise ValueError("overlap must be smaller than the window")
        self._name = model_name
        self.cache_dir = cache_dir
        self.window_tokens = window_tokens
        self.overlap_tokens = overlap_tokens
        self.query_instruction = query_instruction if "bge" in model_name.lower() else ""
        self._model: Any = None
        self._tokenizer: Tokenizer | None = None
        self._dim: int | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return f"{self._name}|w{self.window_tokens}o{self.overlap_tokens}"

    @property
    def dim(self) -> int:
        self._load()
        assert self._dim is not None
        return self._dim

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            from fastembed import TextEmbedding
            from tokenizers import Tokenizer

            self.cache_dir.mkdir(parents=True, exist_ok=True)
            model = TextEmbedding(self._name, cache_dir=str(self.cache_dir))
            # Embedding once guarantees the ONNX model (and its tokenizer) is loaded.
            probe = np.asarray(next(iter(model.embed(["probe"]))))
            shared: Tokenizer | None = getattr(model.model, "tokenizer", None)
            if shared is None:
                raise RuntimeError(f"{self._name}: fastembed exposes no tokenizer to window passages with")
            # A private copy of the model's tokenizer with truncation off: the
            # shared one truncates at 512, which would hide exactly the
            # over-long text windowing exists for.
            tokenizer = Tokenizer.from_str(shared.to_str())
            tokenizer.no_truncation()
            tokenizer.no_padding()
            self._tokenizer, self._dim = tokenizer, int(probe.shape[-1])
            self._model = model
            log.info("loaded embedding model %s (dim %d)", self._name, self._dim)

    def warm(self) -> None:
        """Load the model and run one query, so the first real request is not the slow one."""
        self.embed_query("warm-up")

    def windows(self, text: str) -> list[str]:
        self._load()
        assert self._tokenizer is not None
        encoding = self._tokenizer.encode(text, add_special_tokens=False)
        return token_windows(
            text, encoding.offsets, encoding.word_ids, self.window_tokens, self.overlap_tokens
        )

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        self._load()
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        with self._lock:
            vectors = list(self._model.embed(list(texts), batch_size=EMBED_BATCH))
        return _unit_rows(vectors)

    def embed_query(self, text: str) -> np.ndarray:
        self._load()
        with self._lock:
            vectors = list(self._model.embed([self.query_instruction + text]))
        return _unit_rows(vectors)[0]


def token_windows(
    text: str,
    offsets: Sequence[tuple[int, int]],
    word_ids: Sequence[int | None],
    window: int,
    overlap: int,
) -> list[str]:
    """Cut ``text`` into windows of at most ``window`` tokens overlapping by ~``overlap``.

    ``offsets``/``word_ids`` are the tokenizer's per-token character spans and
    word indexes. Windows are sliced from the original text (decoding token
    ids would lose case and spacing) and never start or end inside a word.
    """
    count = len(offsets)
    if count <= window:
        return [text]

    def word_start(position: int, floor: int) -> int:
        while (
            position > floor + 1
            and word_ids[position] is not None
            and word_ids[position] == word_ids[position - 1]
        ):
            position -= 1
        return position

    pieces: list[str] = []
    start = 0
    while start < count:
        end = min(start + window, count)
        if end < count:
            end = word_start(end, start)
        pieces.append(text[offsets[start][0] : offsets[end - 1][1]])
        if end >= count:
            break
        start = word_start(max(end - overlap, start + 1), start)
    return pieces


@lru_cache(maxsize=1)
def get_embedder() -> FastEmbedEmbedder:
    """The process-wide model. Loading is lazy: importing this module costs nothing."""
    settings = get_settings()
    return FastEmbedEmbedder(settings.embedding_model, settings.data_dir / "models")


# ---------------------------------------------------------------- index


@dataclass(frozen=True)
class WindowVectors:
    """Unit vectors of every window of every chunk, and which chunk each window came from."""

    vectors: np.ndarray  # (windows, dim) float32
    owners: np.ndarray  # (windows,) int64 chunk position

    def __post_init__(self) -> None:
        if len(self.vectors) != len(self.owners):
            raise ValueError("one owner per window vector")


class DenseIndex:
    """Cosine similarity of a query to each chunk: the best of its windows."""

    def __init__(self, windows: WindowVectors, size: int) -> None:
        self.size = size
        self.windows = windows
        dim = windows.vectors.shape[1] if windows.vectors.ndim == 2 else 0
        self._index = faiss.IndexFlatIP(max(dim, 1))
        if len(windows.vectors):
            self._index.add(np.ascontiguousarray(windows.vectors, dtype=np.float32))

    def scores(self, query_vector: np.ndarray) -> np.ndarray:
        """One score per chunk; -inf for a chunk with no windows (empty text)."""
        best = np.full(self.size, -np.inf, dtype=np.float64)
        total = self._index.ntotal
        if total == 0:
            return best
        query = _unit_rows(query_vector)
        sims, ids = self._index.search(query, total)
        valid = ids[0] >= 0
        np.maximum.at(best, self.windows.owners[ids[0][valid]], sims[0][valid].astype(np.float64))
        return best


async def embed_windows(
    embedder: Embedder,
    texts: Sequence[str],
    *,
    on_progress: ProgressFn | None = None,
    batch_size: int = EMBED_BATCH,
) -> WindowVectors:
    """Window and embed ``texts`` off the event loop, reporting progress between batches."""

    def cut() -> tuple[list[str], list[int]]:
        pieces: list[str] = []
        owners: list[int] = []
        for position, text in enumerate(texts):
            for window in embedder.windows(text) if text.strip() else []:
                pieces.append(window)
                owners.append(position)
        return pieces, owners

    pieces, owners = await asyncio.to_thread(cut)
    batches: list[np.ndarray] = []
    for start in range(0, len(pieces), batch_size):
        batch = pieces[start : start + batch_size]
        batches.append(await asyncio.to_thread(embedder.embed_passages, batch))
        if on_progress is not None:
            await on_progress(min(1.0, (start + len(batch)) / len(pieces)))
    vectors = np.concatenate(batches) if batches else np.zeros((0, embedder.dim), dtype=np.float32)
    return WindowVectors(vectors=vectors, owners=np.asarray(owners, dtype=np.int64))


# ---------------------------------------------------------------- on-disk cache


def fingerprint(embedder_name: str, texts: Sequence[str]) -> str:
    """Content address of a paper's window vectors: model, windowing, and every text."""
    digest = hashlib.sha256(embedder_name.encode())
    for text in texts:
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
    return digest.hexdigest()[:24]


class EmbeddingCache:
    """data/cache/embeddings/{paper_id}/{fingerprint}.npz.

    Content-addressed, so it is never stale: re-chunking a paper or changing
    the model changes the fingerprint and simply misses. Safe to delete.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, paper_id: str, key: str) -> Path:
        return self.root / paper_id / f"{key}.npz"

    def load(self, paper_id: str, key: str) -> WindowVectors | None:
        path = self.path(paper_id, key)
        try:
            with np.load(path) as data:
                return WindowVectors(vectors=data["vectors"].astype(np.float32), owners=data["owners"])
        except (FileNotFoundError, KeyError, ValueError, OSError):
            return None

    def save(self, paper_id: str, key: str, windows: WindowVectors) -> None:
        buffer = io.BytesIO()
        np.savez(buffer, vectors=windows.vectors, owners=windows.owners)
        directory = self.root / paper_id
        write_atomic(self.path(paper_id, key), buffer.getvalue())
        # Older fingerprints for this paper are dead weight once a new one lands.
        for stale in directory.glob("*.npz"):
            if stale.stem != key:
                stale.unlink(missing_ok=True)


def default_cache() -> EmbeddingCache:
    return EmbeddingCache(get_settings().cache_dir / "embeddings")


async def load_or_embed(
    embedder: Embedder,
    cache: EmbeddingCache | None,
    paper_id: str,
    texts: Sequence[str],
    *,
    on_progress: ProgressFn | None = None,
) -> WindowVectors:
    """Window vectors for one paper's chunk texts: from the cache, or embedded and cached."""
    key = fingerprint(embedder.name, texts)
    if cache is not None:
        cached = await asyncio.to_thread(cache.load, paper_id, key)
        if cached is not None and (not len(cached.owners) or int(cached.owners.max()) < len(texts)):
            if on_progress is not None:
                await on_progress(1.0)
            return cached
    windows = await embed_windows(embedder, texts, on_progress=on_progress)
    if cache is not None:
        await asyncio.to_thread(cache.save, paper_id, key, windows)
    return windows
