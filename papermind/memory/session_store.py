"""Session state: one JSON document per reading session, 24h TTL.

    session:{session_id}       -> Session JSON, EX settings.session_ttl_seconds
    lock:session:{session_id}  -> random token, NX PX (held only during update())

The API is stateless across uvicorn workers, so two requests for one session
can land on different processes at the same time. The common case is a chat
turn persisting its answer while the reader's scroll PATCH arrives; two browser
tabs on the same session is the other. Each request reads the document,
changes one part of it and writes the whole thing back. Without coordination
the later write silently drops the earlier one's change (a lost update: the
answer vanishes from the transcript, or the scroll position snaps back).
``update()`` therefore runs its read-modify-write under a short per-session
Redis lock. The lock has a PX expiry so a worker that dies mid-update cannot
wedge the session, and release is compare-and-delete so a holder whose lock
already expired never deletes the next holder's lock.
"""

from __future__ import annotations

import asyncio
import random
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from redis.asyncio import Redis
from redis.exceptions import WatchError

from papermind.schemas.session import Session
from papermind.schemas.tools import Level

SessionUpdate = Callable[[Session], Session]


class SessionNotFound(LookupError):
    """No session with this id (never created, or expired after 24h of inactivity)."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"session {session_id!r} not found")
        self.session_id = session_id


class SessionBusy(RuntimeError):
    """The per-session lock stayed taken for longer than the caller was willing to wait."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"session {session_id!r} is locked by another request")
        self.session_id = session_id


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_session_id() -> str:
    return "s_" + secrets.token_urlsafe(12)


def new_message_id() -> str:
    return "m_" + secrets.token_urlsafe(9)


def session_key(session_id: str) -> str:
    return f"session:{session_id}"


def lock_key(session_id: str) -> str:
    return f"lock:session:{session_id}"


def _new_session(user_id: str, paper_id: str, level: Level) -> Session:
    now = utcnow()
    return Session(
        session_id=new_session_id(),
        user_id=user_id,
        paper_id=paper_id,
        level=level,
        created_at=now,
        updated_at=now,
    )


@runtime_checkable
class SessionStore(Protocol):
    backend: str

    async def create(self, user_id: str, paper_id: str, level: Level) -> Session: ...

    async def get(self, session_id: str) -> Session | None: ...

    async def save(self, session: Session) -> None:
        """Write the whole document and refresh its TTL."""
        ...

    async def update(self, session_id: str, fn: SessionUpdate) -> Session:
        """Apply ``fn`` to the current document under the per-session lock and save the result.

        ``fn`` is synchronous on purpose: nothing may await while the lock is held.
        Raises ``SessionNotFound`` or ``SessionBusy``.
        """
        ...


class RedisSessionStore:
    backend = "redis"

    def __init__(
        self,
        redis: Redis,
        *,
        ttl_seconds: int,
        lock_ttl_ms: int = 5_000,
        lock_wait_s: float = 3.0,
    ) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        # The critical section is one GET, a pure function and one SET; 5s is
        # orders of magnitude longer than it takes, so expiry only ever fires
        # for a holder that crashed.
        self._lock_ttl_ms = lock_ttl_ms
        self._lock_wait_s = lock_wait_s

    async def create(self, user_id: str, paper_id: str, level: Level) -> Session:
        session = _new_session(user_id, paper_id, level)
        await self._write(session)
        return session

    async def get(self, session_id: str) -> Session | None:
        raw = await self._redis.get(session_key(session_id))
        return Session.model_validate_json(raw) if raw else None

    async def save(self, session: Session) -> None:
        session.updated_at = utcnow()
        await self._write(session)

    async def update(self, session_id: str, fn: SessionUpdate) -> Session:
        token = await self._acquire(session_id)
        try:
            current = await self.get(session_id)
            if current is None:
                raise SessionNotFound(session_id)
            updated = fn(current)
            await self.save(updated)
            return updated
        finally:
            await self._release(session_id, token)

    async def _write(self, session: Session) -> None:
        await self._redis.set(session_key(session.session_id), session.model_dump_json(), ex=self._ttl)

    async def _acquire(self, session_id: str) -> str:
        token = secrets.token_hex(16)
        key = lock_key(session_id)
        deadline = time.monotonic() + self._lock_wait_s
        delay = 0.01
        while True:
            if await self._redis.set(key, token, nx=True, px=self._lock_ttl_ms):
                return token
            if time.monotonic() >= deadline:
                raise SessionBusy(session_id)
            # Full jitter: contenders that collided once should not retry in lockstep.
            await asyncio.sleep(random.uniform(0, delay))
            delay = min(delay * 2, 0.2)

    async def _release(self, session_id: str, token: str) -> None:
        # Compare-and-delete with WATCH/MULTI rather than a Lua script: same
        # guarantee, and it also runs on fakeredis without the Lua extra.
        key = lock_key(session_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                held = await pipe.get(key)
                if held not in (token, token.encode()):
                    # Our lock expired and someone else holds it now; theirs to release.
                    await pipe.unwatch()
                    return
                pipe.multi()
                pipe.delete(key)
                await pipe.execute()
            except WatchError:
                # The key changed between GET and DEL, so it is no longer ours.
                pass


class MemorySessionStore:
    """In-process store for tests and the dev fallback when Redis is down.

    Same semantics as the Redis store, including the TTL, but nothing survives a
    restart and nothing is shared between workers.
    """

    backend = "memory"

    def __init__(
        self, *, ttl_seconds: int = 24 * 60 * 60, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        # JSON, not model objects: callers mutating a returned Session must not
        # change what is stored, exactly as with Redis.
        self._docs: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def create(self, user_id: str, paper_id: str, level: Level) -> Session:
        session = _new_session(user_id, paper_id, level)
        self._write(session)
        return session

    async def get(self, session_id: str) -> Session | None:
        entry = self._docs.get(session_id)
        if entry is None:
            return None
        raw, expires_at = entry
        if self._clock() >= expires_at:
            del self._docs[session_id]
            self._locks.pop(session_id, None)
            return None
        return Session.model_validate_json(raw)

    async def save(self, session: Session) -> None:
        session.updated_at = utcnow()
        self._write(session)

    async def update(self, session_id: str, fn: SessionUpdate) -> Session:
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            current = await self.get(session_id)
            if current is None:
                raise SessionNotFound(session_id)
            updated = fn(current)
            await self.save(updated)
            return updated

    def ttl_remaining(self, session_id: str) -> float | None:
        entry = self._docs.get(session_id)
        return None if entry is None else max(0.0, entry[1] - self._clock())

    def _write(self, session: Session) -> None:
        self._docs[session.session_id] = (session.model_dump_json(), self._clock() + self._ttl)
