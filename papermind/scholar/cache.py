"""Response cache for Semantic Scholar: Redis when reachable, files otherwise.

Paper metadata changes slowly and S2 is both rate-limited and flaky, so the
cache is what makes this tool usable at all: a reader asking twice about the
same citation should cost zero requests. Values are JSON (the raw S2 bodies, so
a change to our reshaping never serves stale shapes), keys are opaque strings
chosen by the client.

A cache failure must never fail a lookup: every backend error is logged and
treated as a miss.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from papermind.config import Settings, get_settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "s2:"


class Cache(Protocol):
    async def get(self, key: str) -> Any | None:
        """The stored JSON value, or None on a miss (or any backend failure)."""
        ...

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...

    async def aclose(self) -> None: ...


class NullCache:
    """Caches nothing. For tests and for callers that want every request live."""

    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        return None

    async def aclose(self) -> None:
        return None


class RedisCache:
    def __init__(self, redis: Redis, *, prefix: str = KEY_PREFIX) -> None:
        self._redis = redis
        self._prefix = prefix

    async def get(self, key: str) -> Any | None:
        try:
            raw = await self._redis.get(self._prefix + key)
        except (RedisError, OSError) as exc:
            logger.warning("s2 cache read failed (%s); treating as a miss", type(exc).__name__)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            await self._redis.set(
                self._prefix + key, json.dumps(value, separators=(",", ":")), ex=ttl_seconds
            )
        except (RedisError, OSError) as exc:
            logger.warning("s2 cache write failed (%s); continuing uncached", type(exc).__name__)

    async def aclose(self) -> None:
        with contextlib.suppress(RedisError, OSError):
            await self._redis.aclose()


class FileCache:
    """One JSON file per key under ``root``; expiry is stored in the file.

    Disk I/O runs in a worker thread so a slow disk never stalls the event loop.
    Writes go to a temp file and are renamed into place, so a concurrent reader
    (another server process sharing the directory) never sees half a file.
    """

    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.root = root
        self._clock = clock

    def _path(self, key: str) -> Path:
        # Keys contain titles, DOIs, and slashes; hash them into safe filenames.
        return self.root / f"{hashlib.sha256(key.encode()).hexdigest()[:40]}.json"

    async def get(self, key: str) -> Any | None:
        return await asyncio.to_thread(self._get_sync, key)

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        await asyncio.to_thread(self._set_sync, key, value, ttl_seconds)

    async def aclose(self) -> None:
        return None

    def _get_sync(self, key: str) -> Any | None:
        path = self._path(key)
        try:
            entry = json.loads(path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("s2 file cache read failed (%s); treating as a miss", type(exc).__name__)
            return None
        # The key is stored alongside the value so a hash collision reads as a miss.
        if not isinstance(entry, dict) or entry.get("key") != key:
            return None
        if entry.get("expires_at", 0) <= self._clock():
            with contextlib.suppress(OSError):
                path.unlink()
            return None
        return entry.get("value")

    def _set_sync(self, key: str, value: Any, ttl_seconds: int) -> None:
        path = self._path(key)
        entry = {"key": key, "expires_at": self._clock() + ttl_seconds, "value": value}
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-", suffix=".json")
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(json.dumps(entry, separators=(",", ":")).encode())
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        except OSError as exc:
            logger.warning("s2 file cache write failed (%s); continuing uncached", type(exc).__name__)


async def open_cache(settings: Settings | None = None) -> Cache:
    """Redis if it answers a ping, otherwise the file cache under ``cache_dir/s2``."""
    settings = settings or get_settings()
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1.0, socket_timeout=2.0)
    try:
        await redis.ping()
    except (RedisError, OSError) as exc:
        with contextlib.suppress(RedisError, OSError):
            await redis.aclose()
        root = settings.cache_dir / "s2"
        logger.warning(
            "Redis unavailable for the S2 cache (%s); using files under %s", type(exc).__name__, root
        )
        return FileCache(root)
    return RedisCache(redis)
