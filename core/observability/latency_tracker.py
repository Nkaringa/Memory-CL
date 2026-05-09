"""Rolling-window latency tracker with p50/p95/p99 + mean.

Pure-Python, deterministic given the same observation order. Bounded
memory: a fixed-size deque per (metric, shard) tuple. We compute
percentiles by sorting on demand — fine for the window sizes we use
(<= 1024 samples) and avoids dragging in numpy for one helper.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

ScaleMetric = Literal["ingestion", "retrieval", "mcp", "graph", "vector"]


@dataclass(frozen=True, slots=True)
class LatencySnapshot:
    metric: ScaleMetric
    shard_id: str
    sample_count: int
    p50: float
    p95: float
    p99: float
    mean: float
    max: float


class LatencyTracker:
    """Per-(metric, shard) rolling latency window."""

    def __init__(self, *, window_size: int = 256) -> None:
        if window_size <= 0:
            raise ValueError("window_size must be > 0")
        self._window_size = window_size
        self._samples: dict[tuple[str, str], deque[float]] = defaultdict(
            lambda: deque(maxlen=window_size)
        )

    def record(self, *, metric: str, shard_id: str, latency_ms: float) -> None:
        self._samples[(metric, shard_id)].append(float(latency_ms))

    def record_many(
        self, *, metric: str, shard_id: str, latencies: Iterable[float]
    ) -> None:
        bucket = self._samples[(metric, shard_id)]
        for v in latencies:
            bucket.append(float(v))

    def snapshot(self, *, metric: str, shard_id: str) -> LatencySnapshot:
        samples = list(self._samples.get((metric, shard_id), ()))
        if not samples:
            return LatencySnapshot(
                metric=metric, shard_id=shard_id,  # type: ignore[arg-type]
                sample_count=0, p50=0.0, p95=0.0, p99=0.0, mean=0.0, max=0.0,
            )
        ordered = sorted(samples)
        return LatencySnapshot(
            metric=metric, shard_id=shard_id,  # type: ignore[arg-type]
            sample_count=len(ordered),
            p50=_percentile(ordered, 50),
            p95=_percentile(ordered, 95),
            p99=_percentile(ordered, 99),
            mean=sum(ordered) / len(ordered),
            max=ordered[-1],
        )

    def all_snapshots(self) -> list[LatencySnapshot]:
        return [
            self.snapshot(metric=m, shard_id=s)
            for (m, s) in sorted(self._samples)
        ]


def _percentile(sorted_samples: list[float], p: int) -> float:
    if not sorted_samples:
        return 0.0
    if p <= 0:
        return sorted_samples[0]
    if p >= 100:
        return sorted_samples[-1]
    rank = (p / 100) * (len(sorted_samples) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_samples[lo]
    frac = rank - lo
    return sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac


__all__ = ["LatencySnapshot", "LatencyTracker"]
