import asyncio

import pytest

from papermind.scholar import ratelimit
from papermind.scholar.ratelimit import TokenBucket, shared_bucket


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_burst_then_spacing_first_come_first_served() -> None:
    clock = Clock()
    bucket = TokenBucket(rate=0.5, capacity=2, clock=clock)
    # Two immediate, then each later caller is pushed one interval (2 s) further out.
    assert [bucket.reserve() for _ in range(4)] == [0.0, 0.0, 2.0, 4.0]


def test_refills_with_time_up_to_capacity() -> None:
    clock = Clock()
    bucket = TokenBucket(rate=1.0, capacity=1, clock=clock)
    assert bucket.reserve() == 0.0
    assert bucket.reserve() == pytest.approx(1.0)
    clock.now += 10  # long idle: refill is capped at capacity, not 10 tokens
    assert bucket.reserve() == 0.0
    assert bucket.reserve() == pytest.approx(1.0)


def test_max_wait_refuses_without_claiming() -> None:
    clock = Clock()
    bucket = TokenBucket(rate=1.0, capacity=1, clock=clock)
    assert bucket.reserve() == 0.0
    assert bucket.reserve(max_wait=0.5) is None
    # The refused call took nothing, so the next caller still waits only one interval.
    assert bucket.reserve(max_wait=1.0) == pytest.approx(1.0)


def test_rejects_nonsense_configuration() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=0, capacity=1)
    with pytest.raises(ValueError):
        TokenBucket(rate=1, capacity=0.5)


async def test_acquire_sleeps_for_the_reserved_wait() -> None:
    bucket = TokenBucket(rate=50.0, capacity=1)
    loop = asyncio.get_running_loop()
    start = loop.time()
    assert await bucket.acquire()
    assert await bucket.acquire()
    assert loop.time() - start >= 0.015
    assert await bucket.acquire(max_wait=0.0) is False


async def test_acquire_works_across_event_loops() -> None:
    # The process-wide bucket must not bind to whichever loop used it first.
    bucket = TokenBucket(rate=1000.0, capacity=5)
    await bucket.acquire()
    await asyncio.to_thread(asyncio.run, bucket.acquire())


def test_shared_bucket_is_process_wide_and_keyed_by_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ratelimit, "_shared", {})
    keyed = shared_bucket(authenticated=True)
    keyless = shared_bucket(authenticated=False)
    assert shared_bucket(authenticated=True) is keyed
    assert keyed is not keyless
    assert (keyed.rate, keyed.capacity) == (ratelimit.AUTHENTICATED_RATE, ratelimit.AUTHENTICATED_BURST)
    assert (keyless.rate, keyless.capacity) == (
        ratelimit.UNAUTHENTICATED_RATE,
        ratelimit.UNAUTHENTICATED_BURST,
    )
    # A key is a strict 1 request/second; keyless is never faster than that.
    assert keyed.rate == 1.0
    assert keyless.rate <= keyed.rate
