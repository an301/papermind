"""Ingestion progress, shared by every API worker.

    ingest:{paper_id}  -> hash {paper_id, source, title, status, stage, progress, stage_done, stage_total,
                                error, error_code, added_at, updated_at}
    ingest:index       -> sorted set of paper_ids scored by added_at (for listing without SCAN)

Ingestion runs as a background task in whichever worker accepted the upload,
but the library poll can land on any worker, so progress lives in Redis rather
than in the task. Entries expire a day after their last update: a finished
paper is listed from the PaperStore, and a failure nobody retried for a day is
not worth showing any more.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast, runtime_checkable

from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import WatchError

from papermind.schemas.paper import IngestStage, PaperStatus, PaperSummary, StageDetail

INGEST_TTL_SECONDS = 24 * 60 * 60
INDEX_KEY = "ingest:index"
# An in-flight entry that has not moved for this long belongs to a worker that
# died mid-ingest. Without this, the claim would block a retry for a whole day.
# Generous, because a long paper can sit in one stage (indexing) for minutes.
STALE_AFTER_SECONDS = 15 * 60

Source = Literal["arxiv", "upload"]
IN_FLIGHT = frozenset({PaperStatus.QUEUED, PaperStatus.PROCESSING})


def status_key(paper_id: str) -> str:
    return f"ingest:{paper_id}"


def _now() -> datetime:
    return datetime.now(UTC)


class IngestStatus(BaseModel):
    paper_id: str
    source: Source
    title: str = Field(description="best name known so far: the arXiv id or filename until metadata arrives")
    status: PaperStatus
    stage: IngestStage | None = None
    progress: float = Field(0.0, ge=0.0, le=1.0)
    # Counts within the current stage (pages read, passages indexed), when the
    # pipeline reports them. Flat fields because a Redis hash has no nesting.
    stage_done: int | None = Field(None, ge=0)
    stage_total: int | None = Field(None, ge=0)
    error: str | None = None
    error_code: str | None = Field(None, description="AddPaperErrorCode when status is failed")
    added_at: datetime
    updated_at: datetime

    @property
    def in_flight(self) -> bool:
        return self.status in IN_FLIGHT

    def blocks_new_claim(self, now: datetime | None = None) -> bool:
        now = now or _now()
        return self.in_flight and (now - self.updated_at).total_seconds() < STALE_AFTER_SECONDS

    def to_summary(self) -> PaperSummary:
        return PaperSummary(
            paper_id=self.paper_id,
            source=self.source,
            title=self.title,
            status=self.status,
            stage=self.stage,
            progress=self.progress,
            stage_detail=self.stage_detail,
            error=self.error,
            error_code=self.error_code,
            added_at=self.added_at,
        )

    @property
    def stage_detail(self) -> StageDetail | None:
        if self.stage_done is None or self.stage_total is None:
            return None
        return StageDetail(done=min(self.stage_done, self.stage_total), total=self.stage_total)

    def to_hash(self) -> dict[Any, Any]:
        # Typed loosely for redis-py's hset(mapping=...), whose key type is a
        # union that a dict[str, str] does not satisfy (Mapping keys are invariant).
        return {k: "" if v is None else str(v) for k, v in self.model_dump(mode="json").items()}

    @classmethod
    def from_hash(cls, raw: Mapping[str, str]) -> IngestStatus:
        # Redis hashes have no null; the empty string stands in for None.
        return cls.model_validate({k: (v if v != "" else None) for k, v in raw.items()})


@runtime_checkable
class IngestStatusStore(Protocol):
    backend: str

    async def claim(self, paper_id: str, *, source: Source, title: str) -> tuple[IngestStatus, bool]:
        """Register a new ingestion as queued. Returns ``(status, claimed)``.

        ``claimed`` is False when the paper is already queued or processing, in
        which case the existing status comes back and no new job should start.
        A failed, finished or stale entry is replaced (adding a paper again retries it).
        """
        ...

    async def update(
        self,
        paper_id: str,
        *,
        status: PaperStatus | None = None,
        stage: IngestStage | None = None,
        progress: float | None = None,
        stage_detail: StageDetail | None = None,
        error: str | None = None,
        error_code: str | None = None,
        title: str | None = None,
    ) -> bool:
        """Apply the changes. Returns False, changing nothing, when the entry is gone.

        Gone means the paper was removed (on any worker) or the entry expired.
        A job reads False as "stop, and discard what you wrote": that is how a
        DELETE served by one worker reaches an ingestion running on another.
        """
        ...

    async def get(self, paper_id: str) -> IngestStatus | None: ...

    async def list(self) -> list[IngestStatus]:
        """Every live entry, newest first."""
        ...

    async def delete(self, paper_id: str) -> None: ...


def _changes(
    status: PaperStatus | None,
    stage: IngestStage | None,
    progress: float | None,
    stage_detail: StageDetail | None,
    error: str | None,
    error_code: str | None,
    title: str | None,
) -> dict[str, str]:
    fields: dict[str, str] = {"updated_at": _now().isoformat()}
    if status is not None:
        fields["status"] = status.value
        if status is not PaperStatus.FAILED:
            fields["error"] = ""
            fields["error_code"] = ""
    if stage is not None:
        fields["stage"] = stage.value
        # Counts belong to one stage; a new stage without counts must not
        # show the previous stage's "212 of 212".
        if stage_detail is None:
            fields["stage_done"] = fields["stage_total"] = ""
    if stage_detail is not None:
        fields["stage_done"] = str(stage_detail.done)
        fields["stage_total"] = str(stage_detail.total)
    if progress is not None:
        fields["progress"] = str(min(1.0, max(0.0, progress)))
    if error is not None:
        fields["error"] = error
    if error_code is not None:
        fields["error_code"] = error_code
    if title:
        fields["title"] = title
    return fields


def _fresh(paper_id: str, source: Source, title: str) -> IngestStatus:
    now = _now()
    return IngestStatus(
        paper_id=paper_id,
        source=source,
        title=title,
        status=PaperStatus.QUEUED,
        added_at=now,
        updated_at=now,
    )


class RedisIngestStatusStore:
    backend = "redis"

    def __init__(self, redis: Redis, *, ttl_seconds: int = INGEST_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def claim(self, paper_id: str, *, source: Source, title: str) -> tuple[IngestStatus, bool]:
        key = status_key(paper_id)
        # Check-then-write under WATCH: two workers handling the same "add"
        # click (or a double click) must not both start an ingestion job.
        async with self._redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    existing = _parse(await pipe.hgetall(key))
                    if existing is not None and existing.blocks_new_claim():
                        await pipe.unwatch()
                        return existing, False
                    status = _fresh(paper_id, source, title)
                    pipe.multi()
                    pipe.delete(key)
                    pipe.hset(key, mapping=status.to_hash())
                    pipe.expire(key, self._ttl)
                    pipe.zadd(INDEX_KEY, {paper_id: status.added_at.timestamp()})
                    await pipe.execute()
                    return status, True
                except WatchError:
                    continue

    async def update(
        self,
        paper_id: str,
        *,
        status: PaperStatus | None = None,
        stage: IngestStage | None = None,
        progress: float | None = None,
        stage_detail: StageDetail | None = None,
        error: str | None = None,
        error_code: str | None = None,
        title: str | None = None,
    ) -> bool:
        key = status_key(paper_id)
        changes: dict[Any, Any] = _changes(status, stage, progress, stage_detail, error, error_code, title)
        async with self._redis.pipeline(transaction=True) as pipe:
            while True:
                try:
                    await pipe.watch(key)
                    # Never resurrect an entry that was deleted (paper removed
                    # mid-ingest) or expired: HSET would create a partial hash.
                    if not await pipe.exists(key):
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.hset(key, mapping=changes)
                    pipe.expire(key, self._ttl)
                    await pipe.execute()
                    return True
                except WatchError:
                    continue

    async def get(self, paper_id: str) -> IngestStatus | None:
        raw = await self._redis.hgetall(status_key(paper_id))
        return _parse(raw)

    async def list(self) -> list[IngestStatus]:
        cutoff = time.time() - self._ttl
        await self._redis.zremrangebyscore(INDEX_KEY, "-inf", cutoff)
        # The client is created with decode_responses=True, so members are str.
        ids = cast(list[str], await self._redis.zrevrange(INDEX_KEY, 0, -1))
        if not ids:
            return []
        async with self._redis.pipeline(transaction=False) as pipe:
            for pid in ids:
                pipe.hgetall(status_key(pid))
            rows = await pipe.execute()
        found: list[IngestStatus] = []
        gone: list[str] = []
        for pid, raw in zip(ids, rows, strict=True):
            parsed = _parse(raw)
            if parsed is None:
                gone.append(pid)
            else:
                found.append(parsed)
        if gone:
            await self._redis.zrem(INDEX_KEY, *gone)
        return sorted(found, key=lambda s: s.added_at, reverse=True)

    async def delete(self, paper_id: str) -> None:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(status_key(paper_id))
            pipe.zrem(INDEX_KEY, paper_id)
            await pipe.execute()


def _parse(raw: Mapping[Any, Any] | None) -> IngestStatus | None:
    if not raw or "paper_id" not in raw:
        return None
    return IngestStatus.from_hash(raw)


class MemoryIngestStatusStore:
    """In-process twin of the Redis store, for tests and the no-Redis dev fallback."""

    backend = "memory"

    def __init__(self, *, ttl_seconds: int = INGEST_TTL_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: dict[str, tuple[IngestStatus, float]] = {}
        self._lock = asyncio.Lock()

    async def claim(self, paper_id: str, *, source: Source, title: str) -> tuple[IngestStatus, bool]:
        async with self._lock:
            existing = self._live(paper_id)
            if existing is not None and existing.blocks_new_claim():
                return existing.model_copy(), False
            status = _fresh(paper_id, source, title)
            self._entries[paper_id] = (status, self._clock() + self._ttl)
            return status.model_copy(), True

    async def update(
        self,
        paper_id: str,
        *,
        status: PaperStatus | None = None,
        stage: IngestStage | None = None,
        progress: float | None = None,
        stage_detail: StageDetail | None = None,
        error: str | None = None,
        error_code: str | None = None,
        title: str | None = None,
    ) -> bool:
        current = self._live(paper_id)
        if current is None:
            return False
        merged = {
            **current.to_hash(),
            **_changes(status, stage, progress, stage_detail, error, error_code, title),
        }
        self._entries[paper_id] = (IngestStatus.from_hash(merged), self._clock() + self._ttl)
        return True

    async def get(self, paper_id: str) -> IngestStatus | None:
        found = self._live(paper_id)
        return found.model_copy() if found else None

    async def list(self) -> list[IngestStatus]:
        live = [s for pid in list(self._entries) if (s := self._live(pid)) is not None]
        return sorted((s.model_copy() for s in live), key=lambda s: s.added_at, reverse=True)

    async def delete(self, paper_id: str) -> None:
        self._entries.pop(paper_id, None)

    def _live(self, paper_id: str) -> IngestStatus | None:
        entry = self._entries.get(paper_id)
        if entry is None:
            return None
        status, expires_at = entry
        if self._clock() >= expires_at:
            del self._entries[paper_id]
            return None
        return status
