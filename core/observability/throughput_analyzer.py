"""Rolling-window throughput counter (events/second).

The analyzer is fed `record(at)` samples; the snapshot returns the
average events-per-second over the configured window. Time is always
passed in by the caller — never read from the system clock — so two
runs with the same input sequence produce identical numbers.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class ThroughputSnapshot:
    metric: str
    shard_id: str
    window_seconds: float
    event_count: int
    events_per_second: float


class ThroughputAnalyzer:
    """Per-(metric, shard) rolling-window event counter.

    Events older than `window_seconds` are evicted lazily on each
    `record` and `snapshot` call.
    """

    def __init__(self, *, window_seconds: float = 60.0) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._window = window_seconds
        self._events: dict[tuple[str, str], deque[datetime]] = defaultdict(deque)

    def record(self, *, metric: str, shard_id: str, at: datetime) -> None:
        bucket = self._events[(metric, shard_id)]
        bucket.append(at)
        self._evict(bucket, now=at)

    def snapshot(
        self, *, metric: str, shard_id: str, now: datetime,
    ) -> ThroughputSnapshot:
        bucket = self._events.get((metric, shard_id))
        if not bucket:
            return ThroughputSnapshot(
                metric=metric, shard_id=shard_id,
                window_seconds=self._window,
                event_count=0, events_per_second=0.0,
            )
        self._evict(bucket, now=now)
        count = len(bucket)
        return ThroughputSnapshot(
            metric=metric, shard_id=shard_id,
            window_seconds=self._window,
            event_count=count,
            events_per_second=count / self._window,
        )

    def all_snapshots(self, *, now: datetime) -> list[ThroughputSnapshot]:
        return [
            self.snapshot(metric=m, shard_id=s, now=now)
            for (m, s) in sorted(self._events)
        ]

    def _evict(self, bucket: deque[datetime], *, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self._window)
        while bucket and bucket[0] < cutoff:
            bucket.popleft()


__all__ = ["ThroughputAnalyzer", "ThroughputSnapshot"]
