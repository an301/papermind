"""Per-paper indexes: lazy, built once under concurrency, rebuilt when paper.json changes."""

import asyncio
import os
from pathlib import Path

import numpy as np
import pytest

from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.store import PaperStore
from papermind.retrieval.dense import EmbeddingCache
from papermind.retrieval.index import (
    IndexRegistry,
    PaperNotFound,
    normalise_section_filter,
    section_matches,
)
from tests.unit.conftest import TRANSFORMER_LIKE, HashEmbedder, make_record

PDF = b"%PDF-1.4\n%fake\n"


@pytest.fixture
def store(tmp_path: Path) -> PaperStore:
    store = PaperStore(tmp_path / "papers")
    store.save(make_record("1706.03762", TRANSFORMER_LIKE, chunker_version=CHUNKER_VERSION), PDF)
    return store


@pytest.fixture
def registry(store: PaperStore, tmp_path: Path) -> IndexRegistry:
    return IndexRegistry(store, HashEmbedder(), cache=EmbeddingCache(tmp_path / "cache"))


async def test_index_is_built_lazily_and_reused(registry: IndexRegistry):
    assert registry.builds == 0
    index = await registry.get("1706.03762")
    assert registry.builds == 1
    assert [c.section_number for c in index.chunks][:3] == [None, "1", "3"]
    assert index.bm25.size == index.dense.size == len(TRANSFORMER_LIKE)
    assert index.key[:2] == ("1706.03762", CHUNKER_VERSION)
    assert await registry.get("1706.03762") is index
    assert registry.builds == 1


async def test_concurrent_first_requests_build_once(store: PaperStore, tmp_path: Path):
    embedder = HashEmbedder()
    registry = IndexRegistry(store, embedder, cache=None)
    indexes = await asyncio.gather(*(registry.get("1706.03762") for _ in range(8)))
    assert registry.builds == 1
    assert all(index is indexes[0] for index in indexes)
    assert embedder.passage_calls == 1  # the paper was embedded once, not eight times


async def test_a_rewritten_paper_json_gets_a_fresh_index(registry: IndexRegistry, store: PaperStore):
    first = await registry.get("1706.03762")
    record = make_record("1706.03762", [*TRANSFORMER_LIKE, ("6", "Results", "BLEU 28.4 on newstest2014.")])
    store.save(record, PDF)
    # mtime granularity can be coarse; make the rewrite visible regardless.
    path = store.record_path("1706.03762")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    second = await registry.get("1706.03762")
    assert second is not first
    assert len(second.chunks) == len(first.chunks) + 1
    assert registry.builds == 2


async def test_a_deleted_paper_is_not_found_and_its_index_is_dropped(
    registry: IndexRegistry, store: PaperStore
):
    await registry.get("1706.03762")
    store.delete("1706.03762")
    with pytest.raises(PaperNotFound):
        await registry.get("1706.03762")
    assert "1706.03762" not in registry._indexes


async def test_unknown_and_unsafe_paper_ids(registry: IndexRegistry):
    with pytest.raises(PaperNotFound):
        await registry.get("0000.00000")
    with pytest.raises(PaperNotFound):
        await registry.get("../etc")


async def test_least_recently_used_paper_is_evicted(store: PaperStore):
    for paper_id in ("a1", "a2", "a3"):
        store.save(make_record(paper_id, TRANSFORMER_LIKE[:3]), PDF)
    registry = IndexRegistry(store, HashEmbedder(), max_papers=2)
    a1 = await registry.get("a1")
    await registry.get("a2")
    await registry.get("a1")  # a2 is now the least recently used
    await registry.get("a3")
    assert await registry.get("a1") is a1
    assert registry.builds == 3
    await registry.get("a2")
    assert registry.builds == 4


async def test_warm_builds_the_named_papers(registry: IndexRegistry):
    await registry.warm(["1706.03762"])
    assert registry.builds == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3", "3"),
        ("3.2", "3.2"),
        ("sec_3.2", "3.2"),
        ("3.2.", "3.2"),
        (" a.1 ", "A.1"),
        ("", None),
        (None, None),
    ],
)
def test_section_filter_normalisation(raw, expected):
    assert normalise_section_filter(raw) == expected


def test_section_prefix_matching_respects_number_boundaries():
    assert section_matches("3", "3")
    assert section_matches("3.2.1", "3")
    assert section_matches("3.2.1", "3.2")
    assert not section_matches("30", "3")
    assert not section_matches("3.20", "3.2")
    assert not section_matches(None, "3")
    assert section_matches("a.1", "A")


async def test_allowed_mask_follows_the_filter(registry: IndexRegistry):
    index = await registry.get("1706.03762")
    numbers = [c.section_number for c in index.chunks]
    mask = index.allowed("3.2")
    assert [n for n, keep in zip(numbers, mask, strict=True) if keep] == ["3.2.1", "3.2.2"]
    assert index.allowed(None).all()
    assert not index.allowed("9").any()
    assert index.allowed("sec_3.2") is mask  # cached per normalised prefix
    assert isinstance(mask, np.ndarray)
