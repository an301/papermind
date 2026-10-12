"""Shared usage counters for a public deployment: per-visitor rate limits and daily model spend.

Counters live in Redis so every API worker sees the same numbers; without
Redis (dev) each process counts on its own, which is fine for one laptop.
Used by ``papermind.api.guards``; kept here so ``Resources`` can hold them
without importing the API layer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

DAY_S = 24 * 60 * 60


class RateLimiter:
    """Fixed-window counters: at most ``limit`` hits per key per window.

    Fixed windows let a visitor spend two windows' worth across a boundary;
    for a cost guard that is fine, and it costs one INCR per request.
    """

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis
        self._local: dict[str, int] = {}

    async def hit(
        self, bucket: str, key: str, *, limit: int, window_s: int, now: float | None = None
    ) -> float | None:
        """Count one hit. Returns seconds until the window resets if this one is over the limit."""
        now = time.time() if now is None else now
        window = int(now // window_s)
        name = f"rl:{bucket}:{key}:{window}"
        count = await self._incr(name, window_s)
        if count is None or count <= limit:
            return None
        return (window + 1) * window_s - now

    async def blocked(
        self, bucket: str, key: str, *, limit: int, window_s: int, now: float | None = None
    ) -> float | None:
        """Without counting: seconds until the window resets if ``key`` already used ``limit`` hits."""
        now = time.time() if now is None else now
        window = int(now // window_s)
        name = f"rl:{bucket}:{key}:{window}"
        if self._redis is None:
            count = self._local.get(name, 0)
        else:
            try:
                raw = await self._redis.get(name)
            except RedisError:
                log.warning("rate limiter could not reach Redis; letting %s through", name, exc_info=True)
                return None
            count = int(raw) if raw else 0
        return (window + 1) * window_s - now if count >= limit else None

    async def _incr(self, name: str, ttl_s: int) -> int | None:
        if self._redis is None:
            if len(self._local) > 10_000:
                self._local.clear()  # a dev process; old windows are worthless
            self._local[name] = self._local.get(name, 0) + 1
            return self._local[name]
        try:
            count = int(await self._redis.incr(name))
            if count == 1:
                await self._redis.expire(name, ttl_s)
            return count
        except RedisError:
            # Fail open: a Redis blip should not lock every reader out. The
            # daily budget and the Console's spend limit still bound the cost.
            log.warning("rate limiter could not reach Redis; letting %s through", name, exc_info=True)
            return None


class SpendMeter:
    """Model spend per UTC day, added to by every model call (``LLM(on_cost=...)``)."""

    def __init__(self, redis: Redis | None = None, *, budget_usd: float = 0.0) -> None:
        self._redis = redis
        self.budget_usd = budget_usd
        self._local: dict[str, float] = {}
        self._pending: set[asyncio.Task[None]] = set()

    @staticmethod
    def _day(now: datetime | None = None) -> str:
        return (now or datetime.now(UTC)).strftime("%Y-%m-%d")

    def record(self, cost_usd: float) -> None:
        """Synchronous, because the LLM client reports costs from non-async code."""
        if cost_usd <= 0:
            return
        day = self._day()
        self._local[day] = self._local.get(day, 0.0) + cost_usd
        if self._redis is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._add(day, cost_usd))
        except RuntimeError:
            return
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _add(self, day: str, cost_usd: float) -> None:
        assert self._redis is not None
        key = f"spend:{day}"
        try:
            await self._redis.incrbyfloat(key, cost_usd)
            await self._redis.expire(key, 3 * DAY_S)
        except RedisError:
            log.warning("could not record $%.4f of spend in Redis", cost_usd, exc_info=True)

    async def spent_today(self) -> float:
        day = self._day()
        if self._redis is not None:
            try:
                value = await self._redis.get(f"spend:{day}")
                return float(value) if value else 0.0
            except (RedisError, ValueError):
                log.warning(
                    "could not read today's spend from Redis; using this process's count", exc_info=True
                )
        return self._local.get(day, 0.0)

    async def over_budget(self) -> bool:
        return self.budget_usd > 0 and await self.spent_today() >= self.budget_usd

    async def aclose(self) -> None:
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
