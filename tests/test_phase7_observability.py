from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.observability import (
    HealthStatus,
    LatencyTracker,
    SystemHealthMonitor,
    ThroughputAnalyzer,
)
from core.observability.system_health_monitor import HealthThresholds


# =========================================================================
#                              LatencyTracker
# =========================================================================
def test_latency_tracker_returns_zero_when_no_samples() -> None:
    snap = LatencyTracker().snapshot(metric="retrieval", shard_id="s")
    assert snap.sample_count == 0
    assert snap.p99 == 0.0


def test_latency_tracker_percentiles() -> None:
    t = LatencyTracker(window_size=100)
    for v in range(1, 101):  # 1..100
        t.record(metric="retrieval", shard_id="s", latency_ms=float(v))
    snap = t.snapshot(metric="retrieval", shard_id="s")
    assert snap.sample_count == 100
    # p50 ≈ 50, p95 ≈ 95, p99 ≈ 99 (linear interp).
    assert 49.5 <= snap.p50 <= 50.5
    assert 94 <= snap.p95 <= 96
    assert 98 <= snap.p99 <= 100


def test_latency_tracker_window_evicts_oldest() -> None:
    t = LatencyTracker(window_size=3)
    for v in [1, 2, 3, 4]:
        t.record(metric="m", shard_id="s", latency_ms=v)
    snap = t.snapshot(metric="m", shard_id="s")
    assert snap.sample_count == 3  # only last 3 retained
    assert snap.max == 4.0


def test_latency_tracker_isolates_per_metric_and_shard() -> None:
    t = LatencyTracker()
    t.record(metric="retrieval", shard_id="a", latency_ms=10)
    t.record(metric="retrieval", shard_id="b", latency_ms=200)
    a = t.snapshot(metric="retrieval", shard_id="a")
    b = t.snapshot(metric="retrieval", shard_id="b")
    assert a.max == 10
    assert b.max == 200


def test_latency_tracker_rejects_invalid_window() -> None:
    with pytest.raises(ValueError):
        LatencyTracker(window_size=0)


# =========================================================================
#                          ThroughputAnalyzer
# =========================================================================
def test_throughput_returns_zero_when_no_events() -> None:
    snap = ThroughputAnalyzer().snapshot(
        metric="retrieval", shard_id="s",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert snap.event_count == 0
    assert snap.events_per_second == 0.0


def test_throughput_counts_within_window() -> None:
    t = ThroughputAnalyzer(window_seconds=10.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        t.record(metric="m", shard_id="s", at=base + timedelta(seconds=i))
    snap = t.snapshot(metric="m", shard_id="s",
                      now=base + timedelta(seconds=5))
    assert snap.event_count == 5
    assert snap.events_per_second == pytest.approx(0.5, abs=1e-6)


def test_throughput_evicts_old_events() -> None:
    t = ThroughputAnalyzer(window_seconds=2.0)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    t.record(metric="m", shard_id="s", at=base)
    snap = t.snapshot(metric="m", shard_id="s",
                      now=base + timedelta(seconds=10))
    assert snap.event_count == 0


# =========================================================================
#                      SystemHealthMonitor
# =========================================================================
def _monitor() -> SystemHealthMonitor:
    return SystemHealthMonitor(
        latency=LatencyTracker(),
        throughput=ThroughputAnalyzer(),
        thresholds=HealthThresholds(p99_latency_ms=500.0),
    )


def test_health_ok_when_everything_clean() -> None:
    snap = _monitor().evaluate(backend_health={
        "postgres": True, "qdrant": True, "neo4j": True, "redis": True,
    })
    assert snap.status == HealthStatus.OK
    assert snap.backend_failures == ()


def test_health_failed_when_backend_down() -> None:
    snap = _monitor().evaluate(backend_health={
        "postgres": True, "qdrant": False, "neo4j": True, "redis": True,
    })
    assert snap.status == HealthStatus.FAILED
    assert "qdrant" in snap.backend_failures


def test_health_degraded_when_p99_latency_high() -> None:
    monitor = _monitor()
    # Push high latency samples.
    for _ in range(50):
        monitor._latency.record(  # type: ignore[attr-defined]
            metric="retrieval", shard_id="s", latency_ms=999.0,
        )
    snap = monitor.evaluate(backend_health={
        "postgres": True, "qdrant": True, "neo4j": True, "redis": True,
    })
    assert snap.status == HealthStatus.DEGRADED
    assert snap.high_latency_metrics  # non-empty
