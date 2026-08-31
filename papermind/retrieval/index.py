"""Per-paper indexes and the in-memory registry that caches them.

Scoping is per paper because that is what the product does: the agent retrieves
from the paper the user is reading, never across the library. Building one index
per paper rather than one global index with a post-filter is not just tidier --
a global BM25 would compute IDF across every paper, and a global FAISS top-k
would spend most of its budget on chunks that are then thrown away.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from papermind.ingest.models import Chunk
from papermind.ingest.store import load_paper
from papermind.retrieval.bm25 import Bm25Index
from papermind.retrieval.dense import DEFAULT_EMBEDDING_MODEL, DenseIndex, EmbeddingModel

logger = logging.getLogger(__name__)


class PaperIndex:
    """Both retrieval arms over one paper, plus its chunks by id."""

    def __init__(self, paper_id: str, chunks: list[Chunk], model: EmbeddingModel) -> None:
        self.paper_id = paper_id
        self.chunks = chunks
        self.by_id = {chunk.id: chunk for chunk in chunks}
        self.bm25 = Bm25Index(chunks)
        self.dense = DenseIndex(chunks, model)

    def __len__(self) -> int:
        return len(self.chunks)

    def resolve(self, chunk_ids: list[str]) -> list[Chunk]:
        """Ids back to chunks, preserving order and skipping anything unknown."""
        return [self.by_id[cid] for cid in chunk_ids if cid in self.by_id]


class IndexRegistry:
    """Caches one PaperIndex per paper for the process lifetime.

    Indexes are held in memory only. For a few hundred chunks per paper a
    rebuild is a fraction of a second once the embedding model is loaded, so
    persisting them to disk would add a cache-invalidation problem (chunks
    change when CHUNKER_VERSION moves) to save very little.
    """

    def __init__(
        self,
        papers_dir: Path,
        *,
        model: EmbeddingModel | None = None,
        embedding_model: str | None = None,
    ) -> None:
        self._papers_dir = papers_dir
        self._model = model
        self._embedding_model_name = embedding_model or DEFAULT_EMBEDDING_MODEL
        self._indexes: dict[str, PaperIndex] = {}
        # One lock per paper, so a slow build of paper A does not block a
        # request for paper B, and two concurrent first-requests for the same
        # paper build it once.
        self._locks: dict[str, asyncio.Lock] = {}
        self._model_lock = asyncio.Lock()

    @property
    def papers_dir(self) -> Path:
        return self._papers_dir

    async def _get_model(self) -> EmbeddingModel:
        """Load the embedding model once, on first use.

        Shared across every paper's index -- the weights are ~90MB and there is
        no reason to hold more than one copy.
        """
        model = self._model
        if model is not None:
            return model

        async with self._model_lock:
            # Re-check: another coroutine may have loaded it while we waited.
            model = self._model
            if model is not None:
                return model

            name = self._embedding_model_name
            logger.info("loading embedding model %s", name)
            # Loading pulls weights and is CPU-bound; keep it off the event loop.
            model = await asyncio.to_thread(EmbeddingModel, name)
            self._model = model
            return model

    def available_paper_ids(self) -> list[str]:
        """Papers with ingested chunks on disk."""
        if not self._papers_dir.exists():
            return []
        return sorted(
            path.stem
            for path in self._papers_dir.glob("*.json")
            if path.name != "manifest.json"
        )

    def loaded_paper_ids(self) -> list[str]:
        return sorted(self._indexes)

    async def get(self, paper_id: str) -> PaperIndex:
        """Return the paper's index, building and caching it on first use."""
        cached = self._indexes.get(paper_id)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(paper_id, asyncio.Lock())
        async with lock:
            # Re-check: another coroutine may have built it while we waited.
            cached = self._indexes.get(paper_id)
            if cached is not None:
                return cached

            result = await asyncio.to_thread(load_paper, self._papers_dir, paper_id)
            if result is None:
                raise KeyError(
                    f"{paper_id} has not been ingested -- run "
                    f"`python -m papermind.ingest --arxiv-id {paper_id}`"
                )

            model = await self._get_model()
            # Index construction embeds the whole paper: blocking and the
            # slowest step here.
            index = await asyncio.to_thread(PaperIndex, paper_id, result.chunks, model)
            self._indexes[paper_id] = index
            logger.info(
                "indexed %s: %d chunks, %d dense windows",
                paper_id,
                len(index),
                index.dense.n_windows,
            )
            return index

    async def warm(self, paper_ids: list[str] | None = None) -> list[str]:
        """Build indexes ahead of first use, at startup.

        A failure to index one paper must not stop the service from starting --
        the others are still usable, and the broken one raises a clear error
        when it is actually requested.
        """
        targets = paper_ids if paper_ids is not None else self.available_paper_ids()
        built: list[str] = []
        for paper_id in targets:
            try:
                await self.get(paper_id)
                built.append(paper_id)
            except Exception:
                logger.exception("could not index %s; continuing", paper_id)
        return built
