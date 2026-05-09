"""Async micro-task batching with size + time flush triggers.

Used by the Phase-7 distribution layer to fold many small embedding /
upsert calls into a single backend round-trip. Calling code submits
items + awaits the batch result; the engine schedules a flush either
when the batch fills up or after `max_wait_ms` since the first item.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BatchSpec:
    max_size: int
    max_wait_ms: int

    def __post_init__(self) -> None:
        if self.max_size <= 0:
            raise ValueError("max_size must be > 0")
        if self.max_wait_ms <= 0:
            raise ValueError("max_wait_ms must be > 0")


class BatchingEngine[ItemT, ResultT]:
    """Bounded-window batcher.

    Submitter calls `submit(item)` → awaits a per-item future. The
    engine buffers items and flushes via the provided async
    `process_batch(items) -> list[ResultT]`. Order is preserved:
    `result[i]` corresponds to the i-th submitted item in the batch.
    """

    def __init__(
        self,
        *,
        spec: BatchSpec,
        process_batch: Callable[[list[ItemT]], Awaitable[list[ResultT]]],
    ) -> None:
        self._spec = spec
        self._process = process_batch
        self._buffer: list[ItemT] = []
        self._futures: list[asyncio.Future[ResultT]] = []
        self._flush_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def submit(self, item: ItemT) -> ResultT:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ResultT] = loop.create_future()
        async with self._lock:
            self._buffer.append(item)
            self._futures.append(fut)
            if len(self._buffer) >= self._spec.max_size:
                await self._flush_locked()
            elif self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush())
        return await fut

    async def flush(self) -> None:
        async with self._lock:
            if self._buffer:
                await self._flush_locked()

    async def _delayed_flush(self) -> None:
        await asyncio.sleep(self._spec.max_wait_ms / 1000)
        async with self._lock:
            if self._buffer:
                await self._flush_locked()

    async def _flush_locked(self) -> None:
        items = self._buffer
        futures = self._futures
        self._buffer = []
        self._futures = []
        try:
            results = await self._process(items)
        except Exception as exc:
            for f in futures:
                if not f.done():
                    f.set_exception(exc)
            return
        if len(results) != len(futures):
            err = ValueError(
                f"process_batch returned {len(results)} results "
                f"for {len(futures)} items"
            )
            for f in futures:
                if not f.done():
                    f.set_exception(err)
            return
        for f, r in zip(futures, results, strict=True):
            if not f.done():
                f.set_result(r)


__all__ = ["BatchSpec", "BatchingEngine"]
