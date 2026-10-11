"""Fire-and-forget work that still gets cleaned up (cache pre-warms and the like)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger(__name__)


def raise_if_cancel_swallowed() -> None:
    """Re-raise a cancellation that a library call absorbed.

    redis-py (on Python 3.11) can drop a ``CancelledError`` that lands while it
    is acquiring a connection: the command completes and returns normally, and
    the cancel request is left pending on the task with nothing to deliver it.
    Code that must stop when cancelled calls this after such awaits.
    """
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError


class TaskSet:
    """Holds strong references to background tasks until they finish.

    asyncio keeps only weak references to tasks, so an unreferenced
    ``create_task`` can be garbage-collected before it runs to completion.
    Failures are logged here because nobody awaits these tasks.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def __len__(self) -> int:
        return len(self._tasks)

    def spawn(self, coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.warning("background task %s failed: %s", task.get_name(), exc, exc_info=exc)

    async def aclose(self, *, wait_s: float = 5.0) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=wait_s)
