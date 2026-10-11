"""Client-side rate limiting for Semantic Scholar: one token bucket per process.

Official limits (https://www.semanticscholar.org/product/api, checked 2026-10-08
and again 2026-10-09):

* with an API key: "The introductory rate limit for an API key is 1 RPS on all
  endpoints."
* without a key: "1000 requests per second shared among all unauthenticated
  users. Requests may also be further throttled during periods of heavy use."

The keyless pool is shared with everyone on the internet, and in practice it
answers 429 (no Retry-After, just ``x-amzn-errortype: TooManyRequestsException``)
even to one request every few seconds, per endpoint: /paper/{id} and the search
endpoints were refused 8 times out of 8 while /references and /citations still
answered. Our own pace cannot fix that, but there is no reason to add to it:
keyless we send at most one request every two seconds, with a burst of two so a
typical tool call (the paper's record plus its reference list) is not queued.
With a key we hold strictly to 1 request/second, because the limit is per key
and everything sharing the key competes for it.

"Process-wide" is the unit that matters for deployment: the MCP server owns the
bucket, and over stdio every API worker spawns its own server process. With N
workers sharing one key, run the server once over streamable HTTP
(``--http``) so they share one bucket, rather than N buckets at 1 RPS each.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

AUTHENTICATED_RATE = 1.0
AUTHENTICATED_BURST = 1.0
UNAUTHENTICATED_RATE = 0.5
UNAUTHENTICATED_BURST = 2.0


class TokenBucket:
    """Reservation-style token bucket.

    ``reserve`` claims a token synchronously and says how long to wait before
    using it. Tokens may go negative: each waiter claims the next free slot, so
    concurrent callers are spaced out first-come first-served without a queue.
    There is deliberately no asyncio primitive inside -- a process-wide
    ``asyncio.Lock`` binds to the first event loop that contends on it and then
    fails on any other loop. The threading lock is never held across an await.
    """

    def __init__(self, rate: float, capacity: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        if rate <= 0 or capacity < 1:
            raise ValueError("rate must be positive and capacity at least 1")
        self.rate = rate
        self.capacity = capacity
        self._clock = clock
        self._tokens = capacity
        self._updated = clock()
        self._lock = threading.Lock()

    def reserve(self, max_wait: float | None = None) -> float | None:
        """Claim a token; return the wait before using it, or None (nothing claimed) if it exceeds max_wait."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
            self._updated = now
            wait = 0.0 if self._tokens >= 1 else (1 - self._tokens) / self.rate
            if max_wait is not None and wait > max_wait:
                return None
            self._tokens -= 1
            return wait

    async def acquire(self, max_wait: float | None = None) -> bool:
        wait = self.reserve(max_wait)
        if wait is None:
            return False
        if wait > 0:
            await asyncio.sleep(wait)
        return True


_shared: dict[bool, TokenBucket] = {}
_shared_lock = threading.Lock()


def shared_bucket(*, authenticated: bool) -> TokenBucket:
    """The process-wide bucket. Every ScholarClient in the process draws from it."""
    with _shared_lock:
        bucket = _shared.get(authenticated)
        if bucket is None:
            if authenticated:
                bucket = TokenBucket(AUTHENTICATED_RATE, AUTHENTICATED_BURST)
            else:
                bucket = TokenBucket(UNAUTHENTICATED_RATE, UNAUTHENTICATED_BURST)
            _shared[authenticated] = bucket
        return bucket
