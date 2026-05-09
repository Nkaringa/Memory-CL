"""Bounded asyncio worker pool with retry + backoff.

Submitters call `submit(coroutine_fn, *args)` and await the result.
Internally we serialize work through an `asyncio.Semaphore` so the
in-flight count never exceeds `worker_count`. On failure we retry
with exponential backoff (deterministic — backoff sequence depends
only on the attempt number, not wall clock).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(slots=True)
class WorkerStats:
    submitted: int = 0
    completed: int = 0
    failed: int = 0
    retried: int = 0
    inflight: int = 0


class WorkerPool[T]:
    """Bounded-concurrency executor with deterministic retry policy."""

    def __init__(
        self,
        *,
        worker_count: int,
        max_retries: int = 3,
        backoff_base_ms: float = 50.0,
        backoff_factor: float = 2.0,
    ) -> None:
        if worker_count <= 0:
            raise ValueError("worker_count must be > 0")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._capacity = worker_count
        self._sem = asyncio.Semaphore(worker_count)
        self._max_retries = max_retries
        self._backoff_base = backoff_base_ms
        self._backoff_factor = backoff_factor
        self._stats = WorkerStats()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def stats(self) -> WorkerStats:
        # Return a snapshot copy so callers can't mutate live state.
        return WorkerStats(
            submitted=self._stats.submitted,
            completed=self._stats.completed,
            failed=self._stats.failed,
            retried=self._stats.retried,
            inflight=self._stats.inflight,
        )

    async def submit(
        self,
        fn: Callable[..., Awaitable[T]],
        *args,
        **kwargs,
    ) -> T:
        self._stats.submitted += 1
        attempt = 0
        last_exc: BaseException | None = None
        while attempt <= self._max_retries:
            async with self._sem:
                self._stats.inflight += 1
                try:
                    result = await fn(*args, **kwargs)
                    self._stats.completed += 1
                    return result
                except Exception as exc:
                    last_exc = exc
                    if attempt == self._max_retries:
                        self._stats.failed += 1
                        raise
                    self._stats.retried += 1
                finally:
                    self._stats.inflight -= 1
            # Exponential backoff outside the semaphore so other
            # workers can make progress.
            delay = self._backoff_base * (self._backoff_factor ** attempt)
            await asyncio.sleep(delay / 1000)
            attempt += 1
        # Defensive: should be unreachable because the loop always
        # either returns or raises on the final attempt.
        raise last_exc or RuntimeError("worker pool exhausted retries")

    async def map(
        self,
        fn: Callable[..., Awaitable[T]],
        items: list,
    ) -> list[T]:
        """Fan out one task per item, gather results in input order.

        Failures from any single item raise — callers that need
        per-item failure isolation should call `submit` directly and
        handle exceptions in their own gather().
        """
        return await asyncio.gather(*(self.submit(fn, item) for item in items))


__all__ = ["WorkerPool", "WorkerStats"]
