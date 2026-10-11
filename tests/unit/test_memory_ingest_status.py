from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import fakeredis
import pytest

from papermind.memory.ingest_status import (
    INDEX_KEY,
    STALE_AFTER_SECONDS,
    IngestStatusStore,
    MemoryIngestStatusStore,
    RedisIngestStatusStore,
    status_key,
)
from papermind.schemas.paper import IngestStage, PaperStatus


@pytest.fixture(params=["redis", "memory"])
def store(request: pytest.FixtureRequest) -> IngestStatusStore:
    if request.param == "redis":
        return RedisIngestStatusStore(fakeredis.FakeAsyncRedis(decode_responses=True), ttl_seconds=3600)
    return MemoryIngestStatusStore(ttl_seconds=3600)


async def test_claim_registers_a_queued_entry(store: IngestStatusStore) -> None:
    status, claimed = await store.claim("1706.03762", source="arxiv", title="arXiv:1706.03762")
    assert claimed
    assert status.status is PaperStatus.QUEUED
    assert status.progress == 0.0
    assert await store.get("1706.03762") == status


async def test_second_claim_while_in_flight_returns_existing(store: IngestStatusStore) -> None:
    first, _ = await store.claim("p1", source="arxiv", title="one")
    await store.update("p1", status=PaperStatus.PROCESSING, stage=IngestStage.EXTRACTING, progress=0.3)

    again, claimed = await store.claim("p1", source="arxiv", title="one")

    assert not claimed
    assert again.status is PaperStatus.PROCESSING
    assert again.added_at == first.added_at


async def test_concurrent_claims_start_exactly_one_job(store: IngestStatusStore) -> None:
    results = await asyncio.gather(*(store.claim("p1", source="upload", title="f.pdf") for _ in range(10)))
    assert sum(claimed for _, claimed in results) == 1


async def test_failed_entry_can_be_claimed_again(store: IngestStatusStore) -> None:
    await store.claim("p1", source="arxiv", title="one")
    await store.update("p1", status=PaperStatus.FAILED, error="arXiv was down")

    status, claimed = await store.claim("p1", source="arxiv", title="one")

    assert claimed
    assert status.status is PaperStatus.QUEUED
    assert status.error is None


async def test_stale_in_flight_entry_can_be_reclaimed() -> None:
    """A worker that died mid-ingest must not block a retry for the whole TTL."""
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = RedisIngestStatusStore(redis)
    await store.claim("p1", source="arxiv", title="one")
    await store.update("p1", status=PaperStatus.PROCESSING, stage=IngestStage.INDEXING, progress=0.7)
    assert (await store.claim("p1", source="arxiv", title="one"))[1] is False

    long_ago = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS + 5)
    await redis.hset(status_key("p1"), "updated_at", long_ago.isoformat())

    status, claimed = await store.claim("p1", source="arxiv", title="one")
    assert claimed and status.status is PaperStatus.QUEUED


async def test_update_merges_fields_and_clears_error_on_progress(store: IngestStatusStore) -> None:
    await store.claim("p1", source="upload", title="paper.pdf")
    await store.update("p1", status=PaperStatus.PROCESSING, stage=IngestStage.CHUNKING, progress=0.55)
    await store.update("p1", title="Real Title")

    status = await store.get("p1")
    assert status is not None
    assert (status.status, status.stage, status.progress, status.title) == (
        PaperStatus.PROCESSING,
        IngestStage.CHUNKING,
        0.55,
        "Real Title",
    )
    assert status.error is None


async def test_update_never_resurrects_a_deleted_entry(store: IngestStatusStore) -> None:
    await store.claim("p1", source="arxiv", title="one")
    assert await store.update("p1", status=PaperStatus.PROCESSING, progress=0.1) is True
    await store.delete("p1")
    # False is how an ingestion on another worker learns its paper was removed.
    assert await store.update("p1", status=PaperStatus.READY, progress=1.0) is False
    assert await store.get("p1") is None
    assert await store.list() == []


async def test_list_is_newest_first(store: IngestStatusStore) -> None:
    for pid in ("a", "b", "c"):
        await store.claim(pid, source="arxiv", title=pid)
        await asyncio.sleep(0.002)
    assert [s.paper_id for s in await store.list()] == ["c", "b", "a"]


async def test_summary_carries_progress(store: IngestStatusStore) -> None:
    await store.claim("p1", source="upload", title="paper.pdf")
    await store.update("p1", status=PaperStatus.FAILED, error="That file has no text layer.")
    summary = (await store.get("p1")).to_summary()  # type: ignore[union-attr]
    assert summary.status is PaperStatus.FAILED
    assert summary.error == "That file has no text layer."
    assert summary.source == "upload"


async def test_redis_entries_expire_and_index_is_pruned() -> None:
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = RedisIngestStatusStore(redis, ttl_seconds=3600)
    await store.claim("p1", source="arxiv", title="one")
    assert 0 < await redis.ttl(status_key("p1")) <= 3600

    await redis.delete(status_key("p1"))  # as if it expired
    assert await store.list() == []
    assert await redis.zcard(INDEX_KEY) == 0


async def test_memory_entries_expire() -> None:
    now = [0.0]
    store = MemoryIngestStatusStore(ttl_seconds=10, clock=lambda: now[0])
    await store.claim("p1", source="arxiv", title="one")
    now[0] = 11.0
    assert await store.get("p1") is None
