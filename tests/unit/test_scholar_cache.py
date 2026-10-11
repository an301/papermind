import json
from pathlib import Path

import pytest
from fakeredis import FakeAsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from papermind.config import Settings
from papermind.scholar import cache as cache_module
from papermind.scholar.cache import FileCache, NullCache, RedisCache, open_cache


class WallClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def entries(root: Path) -> list[Path]:
    return sorted(root.glob("*.json"))


# ---------------------------------------------------------------- file cache


async def test_file_cache_round_trip_and_miss(tmp_path: Path) -> None:
    cache = FileCache(tmp_path / "s2")
    assert await cache.get("paper/ARXIV:1706.03762:abc") is None
    value = {"paperId": "x", "title": "T", "nested": [1, 2, {"a": None}]}
    await cache.set("paper/ARXIV:1706.03762:abc", value, ttl_seconds=60)
    assert await cache.get("paper/ARXIV:1706.03762:abc") == value
    # Keys with slashes and colons are hashed into flat, safe filenames.
    files = entries(tmp_path / "s2")
    assert len(files) == 1
    assert files[0].suffix == ".json"
    assert "/" not in files[0].name and ":" not in files[0].name


async def test_file_cache_expires_and_removes_the_file(tmp_path: Path) -> None:
    clock = WallClock()
    cache = FileCache(tmp_path, clock=clock)
    await cache.set("k", {"v": 1}, ttl_seconds=10)
    clock.now += 9
    assert await cache.get("k") == {"v": 1}
    clock.now += 2
    assert await cache.get("k") is None
    assert entries(tmp_path) == []


async def test_file_cache_treats_corrupt_or_foreign_files_as_misses(tmp_path: Path) -> None:
    cache = FileCache(tmp_path)
    await cache.set("k", {"v": 1}, ttl_seconds=60)
    (path,) = entries(tmp_path)

    path.write_text("{not json")
    assert await cache.get("k") is None

    # Same filename, different key inside (a hash collision): never served.
    path.write_text(json.dumps({"key": "other", "expires_at": 9e18, "value": {"v": 2}}))
    assert await cache.get("k") is None


async def test_file_cache_write_failure_is_swallowed(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    cache = FileCache(blocker / "s2")  # mkdir under a file fails
    await cache.set("k", {"v": 1}, ttl_seconds=60)
    assert await cache.get("k") is None


# ---------------------------------------------------------------- redis cache


async def test_redis_cache_prefix_ttl_and_json() -> None:
    redis = FakeAsyncRedis()
    cache = RedisCache(redis)
    await cache.set("paper/x:1", {"paperId": "x"}, ttl_seconds=120)
    assert await cache.get("paper/x:1") == {"paperId": "x"}
    assert await redis.exists("s2:paper/x:1") == 1
    assert 0 < await redis.ttl("s2:paper/x:1") <= 120
    stored = await redis.get("s2:paper/x:1")
    assert stored is not None and json.loads(stored) == {"paperId": "x"}
    assert await cache.get("missing") is None
    await cache.aclose()


class BrokenRedis:
    async def get(self, key: str) -> bytes:
        raise RedisConnectionError("down")

    async def set(self, *args: object, **kwargs: object) -> None:
        raise RedisConnectionError("down")

    async def aclose(self) -> None:
        raise RedisConnectionError("down")


async def test_redis_failures_degrade_to_misses() -> None:
    cache = RedisCache(BrokenRedis())  # type: ignore[arg-type]
    await cache.set("k", {"v": 1}, ttl_seconds=60)
    assert await cache.get("k") is None
    await cache.aclose()


async def test_null_cache_stores_nothing() -> None:
    cache = NullCache()
    await cache.set("k", 1, ttl_seconds=60)
    assert await cache.get("k") is None


# ---------------------------------------------------------------- selection


class PingableRedis:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.closed = False

    async def ping(self) -> bool:
        if not self.ok:
            raise RedisConnectionError("connection refused")
        return True

    async def aclose(self) -> None:
        self.closed = True


async def test_open_cache_prefers_redis(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = PingableRedis(ok=True)
    monkeypatch.setattr(cache_module.Redis, "from_url", staticmethod(lambda *a, **k: fake))
    cache = await open_cache(Settings(_env_file=None, data_dir=tmp_path))
    assert isinstance(cache, RedisCache)


async def test_open_cache_falls_back_to_files_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = PingableRedis(ok=False)
    monkeypatch.setattr(cache_module.Redis, "from_url", staticmethod(lambda *a, **k: fake))
    cache = await open_cache(Settings(_env_file=None, data_dir=tmp_path))
    assert isinstance(cache, FileCache)
    assert cache.root == tmp_path / "cache" / "s2"
    assert fake.closed
