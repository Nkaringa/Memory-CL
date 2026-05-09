"""Priority queue → worker-pool dispatcher.

Higher `TaskPriority` values run first. Within a priority band, FIFO
order is preserved by encoding insertion order into the queue tuple.
This gives us a fully deterministic dispatch sequence: replaying the
same submit order yields the same execution order.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from infra.distributed.worker_pool import WorkerPool


class TaskPriority(IntEnum):
    BACKGROUND = 0
    NORMAL = 5
    HIGH = 10
    CRITICAL = 15


@dataclass(slots=True, order=True)
class PriorityTask:
    sort_key: tuple[int, int] = field(init=False)
    priority: TaskPriority
    seq: int
    fn: Callable[..., Awaitable[Any]] = field(compare=False)
    args: tuple = field(default=(), compare=False)
    kwargs: dict = field(default_factory=dict, compare=False)
    future: asyncio.Future = field(init=False, compare=False)

    def __post_init__(self) -> None:
        # heapq is a min-heap, so we negate priority to get
        # highest-first ordering. `seq` breaks ties for FIFO.
        self.sort_key = (-int(self.priority), self.seq)
        self.future = asyncio.get_event_loop().create_future()


class TaskScheduler:
    """Single-process priority dispatcher backed by a `WorkerPool`."""

    def __init__(self, *, pool: WorkerPool) -> None:
        self._pool = pool
        self._heap: list[PriorityTask] = []
        self._counter = itertools.count()
        self._dispatch_lock = asyncio.Lock()
        self._pending_drain: asyncio.Task | None = None

    async def submit(
        self,
        fn: Callable[..., Awaitable[Any]],
        *args,
        priority: TaskPriority = TaskPriority.NORMAL,
        **kwargs,
    ) -> Any:
        task = PriorityTask(
            priority=priority, seq=next(self._counter),
            fn=fn, args=args, kwargs=kwargs,
        )
        heapq.heappush(self._heap, task)
        # Kick off a drain if one isn't already running.
        if self._pending_drain is None or self._pending_drain.done():
            self._pending_drain = asyncio.create_task(self._drain())
        return await task.future

    async def _drain(self) -> None:
        async with self._dispatch_lock:
            # Hold strong refs to fan-out tasks so the event loop's
            # weak-ref bookkeeping doesn't garbage-collect them mid-flight.
            inflight: set[asyncio.Task] = set()
            while self._heap:
                task = heapq.heappop(self._heap)
                # Each task runs through the worker pool — this is
                # what bounds total concurrency across submissions.
                async def _run(t: PriorityTask = task) -> None:
                    try:
                        result = await self._pool.submit(t.fn, *t.args, **t.kwargs)
                        if not t.future.done():
                            t.future.set_result(result)
                    except Exception as exc:
                        if not t.future.done():
                            t.future.set_exception(exc)
                run_task = asyncio.create_task(_run())
                inflight.add(run_task)
                run_task.add_done_callback(inflight.discard)


__all__ = ["PriorityTask", "TaskPriority", "TaskScheduler"]
