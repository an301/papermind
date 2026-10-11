"""Per-paper retrieval indexes, built lazily and cached in the retriever process.

An index is keyed by ``(paper_id, CHUNKER_VERSION, paper.json mtime)``: a
re-ingested paper (new mtime) or a different chunker gets a fresh index on the
next request, with no invalidation protocol between the API process and this
one. Building means loading paper.json, a BM25 pass (milliseconds) and the
window vectors, which ingestion has normally already cached on disk.

Concurrent first requests for one paper -- the agent fires several searches in
parallel -- must not each embed the paper. A per-paper ``asyncio.Lock`` makes
the first one build and the rest wait for it.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.store import InvalidPaperId, PaperStore
from papermind.retrieval.bm25 import DEFAULT_VARIANT, BM25Index, BM25Variant
from papermind.retrieval.dense import DenseIndex, Embedder, EmbeddingCache, load_or_embed
from papermind.retrieval.text import indexable_text
from papermind.schemas.paper import Chunk

log = logging.getLogger(__name__)

IndexKey = tuple[str, str, int]


class PaperNotFound(LookupError):
    """No ingested paper with this id (or it has no chunks to search)."""


def normalise_section_filter(section_filter: str | None) -> str | None:
    """'3.2', 'sec_3.2', '3.2.', ' a.1 ' -> '3.2' / 'A.1'; blank -> None."""
    if section_filter is None:
        return None
    value = section_filter.strip()
    if value.lower().startswith("sec_"):
        value = value[4:]
    value = value.strip(". ").upper()
    return value or None


def section_matches(number: str | None, prefix: str) -> bool:
    """Is a section numbered ``number`` inside ``prefix``? '3' holds '3', '3.1', '3.2.1' -- not '30'."""
    if number is None:
        return False
    number = number.upper()
    return number == prefix or number.startswith(prefix + ".")


@dataclass
class PaperIndex:
    paper_id: str
    key: IndexKey
    chunks: list[Chunk]
    bm25: BM25Index
    dense: DenseIndex
    chunker_version: str = CHUNKER_VERSION
    _masks: dict[str | None, np.ndarray] = field(default_factory=dict, repr=False)

    def allowed(self, section_filter: str | None) -> np.ndarray:
        """Boolean mask over chunks that a search with this filter may return."""
        prefix = normalise_section_filter(section_filter)
        mask = self._masks.get(prefix)
        if mask is None:
            if prefix is None:
                mask = np.ones(len(self.chunks), dtype=bool)
            else:
                mask = np.array([section_matches(c.section_number, prefix) for c in self.chunks], dtype=bool)
            self._masks[prefix] = mask
        return mask


class IndexRegistry:
    """Lazily built, LRU-cached ``PaperIndex`` per paper."""

    def __init__(
        self,
        store: PaperStore,
        embedder: Embedder,
        *,
        cache: EmbeddingCache | None = None,
        max_papers: int = 16,
        bm25_variant: BM25Variant = DEFAULT_VARIANT,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.cache = cache
        self.max_papers = max_papers
        self.bm25_variant = bm25_variant
        self._indexes: OrderedDict[str, PaperIndex] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self.builds = 0  # how many indexes were built; tests assert concurrent requests build once

    async def _key(self, paper_id: str) -> IndexKey:
        try:
            path = self.store.record_path(paper_id)
            stat = await asyncio.to_thread(path.stat)
        except (InvalidPaperId, FileNotFoundError) as exc:
            # A deleted paper's index would otherwise sit in memory until evicted.
            self._indexes.pop(paper_id, None)
            raise PaperNotFound(f"no ingested paper with id {paper_id!r}") from exc
        return (paper_id, CHUNKER_VERSION, stat.st_mtime_ns)

    def _cached(self, paper_id: str, key: IndexKey) -> PaperIndex | None:
        index = self._indexes.get(paper_id)
        if index is not None and index.key == key:
            self._indexes.move_to_end(paper_id)
            return index
        return None

    async def get(self, paper_id: str) -> PaperIndex:
        key = await self._key(paper_id)
        index = self._cached(paper_id, key)
        if index is not None:
            return index
        lock = self._locks.setdefault(paper_id, asyncio.Lock())
        async with lock:
            # Whoever held the lock may have built it while this request waited.
            key = await self._key(paper_id)
            index = self._cached(paper_id, key)
            if index is None:
                index = await self._build(paper_id, key)
                self._indexes[paper_id] = index
                self._indexes.move_to_end(paper_id)
                while len(self._indexes) > self.max_papers:
                    evicted, _ = self._indexes.popitem(last=False)
                    log.info("evicted retrieval index for %s", evicted)
            return index

    async def _build(self, paper_id: str, key: IndexKey) -> PaperIndex:
        record = await asyncio.to_thread(self.store.load, paper_id)
        if record is None:
            raise PaperNotFound(f"no ingested paper with id {paper_id!r}")
        if record.meta.chunker_version != CHUNKER_VERSION:
            log.warning(
                "%s was chunked by chunker v%s (this process is v%s); re-ingest it to refresh chunk ids",
                paper_id,
                record.meta.chunker_version,
                CHUNKER_VERSION,
            )
        texts = [indexable_text(chunk) for chunk in record.chunks]
        bm25 = await asyncio.to_thread(BM25Index, texts, variant=self.bm25_variant)
        windows = await load_or_embed(self.embedder, self.cache, paper_id, texts)
        dense = await asyncio.to_thread(DenseIndex, windows, len(texts))
        self.builds += 1
        log.info(
            "built retrieval index for %s: %d chunks, %d windows", paper_id, len(texts), len(windows.owners)
        )
        return PaperIndex(paper_id=paper_id, key=key, chunks=list(record.chunks), bm25=bm25, dense=dense)

    async def warm(self, paper_ids: Sequence[str]) -> None:
        for paper_id in paper_ids:
            await self.get(paper_id)
