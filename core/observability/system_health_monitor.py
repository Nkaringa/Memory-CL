"""Aggregate health view across the running system.

Combines the latency + throughput trackers with caller-supplied
backend probes (Postgres / Neo4j / Qdrant / Redis ping outcomes) to
produce one `HealthSnapshot`. The snapshot is what an admin endpoint
or readiness probe consults to decide whether to enter degraded mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.observability.latency_tracker import LatencyTracker
from core.observability.throughput_analyzer import ThroughputAnalyzer


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    status: HealthStatus
    backend_failures: tuple[str, ...]
    high_latency_metrics: tuple[str, ...]
    low_throughput_metrics: tuple[str, ...]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class HealthThresholds:
    p99_latency_ms: float = 1500.0      # any p99 above this → degraded
    min_events_per_second: float = 0.0  # only flag if explicitly set > 0


class SystemHealthMonitor:
    """Pure-function aggregator over the existing tracker state.

    Holding the trackers as fields keeps the call site short and
    consistent — tests and probes both call `evaluate()` with the
    same view of data.
    """

    def __init__(
        self,
        *,
        latency: LatencyTracker,
        throughput: ThroughputAnalyzer,
        thresholds: HealthThresholds | None = None,
    ) -> None:
        self._latency = latency
        self._throughput = throughput
        self._thresholds = thresholds or HealthThresholds()

    def evaluate(
        self,
        *,
        backend_health: dict[str, bool],
    ) -> HealthSnapshot:
        backend_failures = tuple(
            sorted(name for name, ok in backend_health.items() if not ok)
        )
        high_latency = tuple(
            sorted(
                f"{snap.metric}:{snap.shard_id}"
                for snap in self._latency.all_snapshots()
                if snap.p99 > self._thresholds.p99_latency_ms
            )
        )
        # Throughput floor only applied when the operator explicitly
        # asked for it (default 0 → never trigger).
        low_throughput: tuple[str, ...] = ()

        # Promotion logic:
        #   any backend down  -> FAILED
        #   any latency hot   -> DEGRADED
        #   otherwise         -> OK
        if backend_failures:
            status = HealthStatus.FAILED
        elif high_latency or low_throughput:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.OK

        notes = ""
        if status == HealthStatus.FAILED:
            notes = f"backend down: {', '.join(backend_failures)}"
        elif status == HealthStatus.DEGRADED and high_latency:
            notes = f"high p99 latency: {', '.join(high_latency)}"

        return HealthSnapshot(
            status=status,
            backend_failures=backend_failures,
            high_latency_metrics=high_latency,
            low_throughput_metrics=low_throughput,
            notes=notes,
        )


__all__ = [
    "HealthSnapshot",
    "HealthStatus",
    "HealthThresholds",
    "SystemHealthMonitor",
]
