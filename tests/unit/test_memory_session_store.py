from __future__ import annotations

import asyncio

import fakeredis
import pytest

from papermind.memory.session_store import (
    MemorySessionStore,
    RedisSessionStore,
    SessionBusy,
    SessionNotFound,
    lock_key,
    session_key,
    utcnow,
)
from papermind.schemas.session import ChatMessage, ScrollPosition, Session

TTL = 3600


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


@pytest.fixture
def store(redis: fakeredis.FakeAsyncRedis) -> RedisSessionStore:
    return RedisSessionStore(redis, ttl_seconds=TTL, lock_wait_s=0.3)


def _message(text: str) -> ChatMessage:
    return ChatMessage(message_id=f"m_{text}", role="user", text=text, created_at=utcnow())


def _append(text: str):
    def fn(s: Session) -> Session:
        return s.model_copy(update={"messages": [*s.messages, _message(text)]})

    return fn


async def test_create_and_get_round_trip(store: RedisSessionStore) -> None:
    created = await store.create("u_1", "1706.03762", "beginner")
    assert created.session_id.startswith("s_")
    loaded = await store.get(created.session_id)
    assert loaded == created
    assert await store.get("s_missing") is None


async def test_save_refreshes_ttl(store: RedisSessionStore, redis: fakeredis.FakeAsyncRedis) -> None:
    session = await store.create("u_1", "p", "intermediate")
    key = session_key(session.session_id)
    assert 0 < await redis.ttl(key) <= TTL

    await redis.expire(key, 10)
    session.scroll = ScrollPosition(page=3)
    await store.save(session)

    assert await redis.ttl(key) > TTL - 5
    assert (await store.get(session.session_id)).scroll.page == 3  # type: ignore[union-attr]


async def test_update_applies_fn_and_refreshes_ttl(
    store: RedisSessionStore, redis: fakeredis.FakeAsyncRedis
) -> None:
    session = await store.create("u_1", "p", "intermediate")
    await redis.expire(session_key(session.session_id), 10)

    updated = await store.update(session.session_id, _append("hello"))

    assert [m.text for m in updated.messages] == ["hello"]
    assert updated.updated_at >= session.updated_at
    assert await redis.ttl(session_key(session.session_id)) > TTL - 5
    assert await redis.exists(lock_key(session.session_id)) == 0, "lock must be released"


async def test_update_missing_session_raises_and_releases_lock(
    store: RedisSessionStore, redis: fakeredis.FakeAsyncRedis
) -> None:
    with pytest.raises(SessionNotFound):
        await store.update("s_gone", _append("x"))
    assert await redis.exists(lock_key("s_gone")) == 0


async def test_concurrent_updates_are_not_lost(
    store: RedisSessionStore, redis: fakeredis.FakeAsyncRedis
) -> None:
    """The read-modify-write race the lock exists for: every append must survive."""
    patient = RedisSessionStore(redis, ttl_seconds=TTL, lock_wait_s=10.0)
    session = await patient.create("u_1", "p", "intermediate")

    # Each update awaits Redis three times, so without the lock these
    # interleave and most appends overwrite each other.
    await asyncio.gather(*(patient.update(session.session_id, _append(str(i))) for i in range(25)))

    final = await store.get(session.session_id)
    assert final is not None
    assert sorted(m.text for m in final.messages) == sorted(str(i) for i in range(25))


async def test_lock_contention_times_out_with_session_busy(
    store: RedisSessionStore, redis: fakeredis.FakeAsyncRedis
) -> None:
    session = await store.create("u_1", "p", "intermediate")
    await redis.set(lock_key(session.session_id), "someone-else", px=10_000)

    with pytest.raises(SessionBusy):
        await store.update(session.session_id, _append("x"))

    # The other holder's lock is untouched and the document unchanged.
    assert await redis.get(lock_key(session.session_id)) == "someone-else"
    assert (await store.get(session.session_id)).messages == []  # type: ignore[union-attr]


async def test_waiter_proceeds_once_lock_is_released(
    store: RedisSessionStore, redis: fakeredis.FakeAsyncRedis
) -> None:
    session = await store.create("u_1", "p", "intermediate")
    await redis.set(lock_key(session.session_id), "holder", px=10_000)

    async def release_soon() -> None:
        await asyncio.sleep(0.05)
        await redis.delete(lock_key(session.session_id))

    _, updated = await asyncio.gather(release_soon(), store.update(session.session_id, _append("after")))
    assert [m.text for m in updated.messages] == ["after"]


async def test_release_never_deletes_a_lock_that_is_no_longer_ours(
    store: RedisSessionStore, redis: fakeredis.FakeAsyncRedis
) -> None:
    session = await store.create("u_1", "p", "intermediate")
    token = await store._acquire(session.session_id)
    # Our lock expired mid-update and another request took it.
    await redis.set(lock_key(session.session_id), "next-holder", px=10_000)

    await store._release(session.session_id, token)

    assert await redis.get(lock_key(session.session_id)) == "next-holder"


async def test_memory_store_ttl_and_refresh() -> None:
    now = [1000.0]
    store = MemorySessionStore(ttl_seconds=100, clock=lambda: now[0])
    session = await store.create("u_1", "p", "expert")

    now[0] += 90
    await store.update(session.session_id, _append("still here"))
    now[0] += 90  # 180s after creation, 90s after the refresh
    assert await store.get(session.session_id) is not None

    now[0] += 11
    assert await store.get(session.session_id) is None
    with pytest.raises(SessionNotFound):
        await store.update(session.session_id, _append("x"))


async def test_memory_store_returns_copies() -> None:
    store = MemorySessionStore()
    session = await store.create("u_1", "p", "expert")
    loaded = await store.get(session.session_id)
    assert loaded is not None
    loaded.messages.append(_message("not saved"))
    assert (await store.get(session.session_id)).messages == []  # type: ignore[union-attr]


async def test_memory_store_serialises_concurrent_updates() -> None:
    store = MemorySessionStore()
    session = await store.create("u_1", "p", "expert")
    await asyncio.gather(*(store.update(session.session_id, _append(str(i))) for i in range(20)))
    final = await store.get(session.session_id)
    assert final is not None and len(final.messages) == 20
