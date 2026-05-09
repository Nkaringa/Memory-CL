from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from core.performance import (
    BackpressureController,
    BatchingEngine,
    RateLimiter,
    ThrottleLevel,
)
from core.performance.batching_engine import BatchSpec


# =========================================================================
#                              RateLimiter
# =========================================================================
def test_rate_limiter_allows_within_burst() -> None:
    rl = RateLimiter(rate_per_second=10.0, burst=5)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for _ in range(5):
        assert rl.acquire(caller="c", resource="r", now=now).allowed


def test_rate_limiter_blocks_when_bucket_drained() -> None:
    rl = RateLimiter(rate_per_second=10.0, burst=2)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for _ in range(2):
        assert rl.acquire(caller="c", resource="r", now=now).allowed
    decision = rl.acquire(caller="c", resource="r", now=now)
    assert not decision.allowed
    assert decision.retry_after_ms > 0


def test_rate_limiter_refills_over_time() -> None:
    rl = RateLimiter(rate_per_second=10.0, burst=1)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    rl.acquire(caller="c", resource="r", now=base)  # drains
    blocked = rl.acquire(caller="c", resource="r", now=base)
    assert not blocked.allowed
    # Advance 200ms → 2 tokens accrued (rate 10/s).
    later = base + timedelta(milliseconds=200)
    refilled = rl.acquire(caller="c", resource="r", now=later)
    assert refilled.allowed


def test_rate_limiter_isolates_per_caller_resource_pair() -> None:
    rl = RateLimiter(rate_per_second=1.0, burst=1)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert rl.acquire(caller="a", resource="x", now=now).allowed
    # Different caller still has its own bucket.
    assert rl.acquire(caller="b", resource="x", now=now).allowed
    # But same caller exhausted.
    assert not rl.acquire(caller="a", resource="x", now=now).allowed


def test_rate_limiter_rejects_invalid_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(rate_per_second=0)


def test_rate_limiter_is_deterministic_for_identical_streams() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    a = RateLimiter(rate_per_second=10.0, burst=3)
    b = RateLimiter(rate_per_second=10.0, burst=3)
    decisions_a = [a.acquire(caller="c", resource="r", now=base).allowed
                   for _ in range(5)]
    decisions_b = [b.acquire(caller="c", resource="r", now=base).allowed
                   for _ in range(5)]
    assert decisions_a == decisions_b


# =========================================================================
#                          BackpressureController
# =========================================================================
def test_backpressure_no_throttle_below_threshold() -> None:
    bp = BackpressureController(queue_threshold=0.8, inflight_threshold=0.9)
    snap = bp.evaluate(
        queue_depth=10, queue_capacity=100, inflight=5, inflight_capacity=20,
    )
    assert snap.level == ThrottleLevel.NONE
    assert not snap.degraded


def test_backpressure_level_1_at_threshold() -> None:
    bp = BackpressureController(queue_threshold=0.8, inflight_threshold=0.9)
    snap = bp.evaluate(
        queue_depth=80, queue_capacity=100,
        inflight=5, inflight_capacity=20,
    )
    assert snap.level == ThrottleLevel.INGESTION
    assert bp.should_throttle(snap.level, "ingestion")
    assert not bp.should_throttle(snap.level, "retrieval")
    assert not bp.should_throttle(snap.level, "graph")


def test_backpressure_escalates_with_pressure() -> None:
    bp = BackpressureController(queue_threshold=0.5, inflight_threshold=0.5)
    light = bp.evaluate(queue_depth=50, queue_capacity=100,
                        inflight=0, inflight_capacity=10)
    medium = bp.evaluate(queue_depth=80, queue_capacity=100,
                         inflight=0, inflight_capacity=10)
    heavy = bp.evaluate(queue_depth=100, queue_capacity=100,
                        inflight=0, inflight_capacity=10)
    assert light.level == ThrottleLevel.INGESTION
    assert medium.level == ThrottleLevel.INGESTION_AND_RETRIEVAL
    assert heavy.level == ThrottleLevel.INGESTION_AND_RETRIEVAL_AND_MCP


def test_backpressure_never_throttles_graph_layer() -> None:
    """Spec-mandated invariant: NEVER drop graph integrity."""
    bp = BackpressureController(queue_threshold=0.1, inflight_threshold=0.1)
    snap = bp.evaluate(queue_depth=999, queue_capacity=100,
                       inflight=999, inflight_capacity=10)
    # Even at the highest level, graph never gets throttled.
    assert snap.level == ThrottleLevel.INGESTION_AND_RETRIEVAL_AND_MCP
    assert not bp.should_throttle(snap.level, "graph")


def test_backpressure_rejects_invalid_thresholds() -> None:
    with pytest.raises(ValueError):
        BackpressureController(queue_threshold=1.5)
    with pytest.raises(ValueError):
        BackpressureController(inflight_threshold=-0.1)


# =========================================================================
#                              BatchingEngine
# =========================================================================
@pytest.mark.asyncio
async def test_batching_flushes_at_max_size() -> None:
    flushes: list[list[int]] = []

    async def process(items: list[int]) -> list[int]:
        flushes.append(list(items))
        return [i * 2 for i in items]

    engine = BatchingEngine[int, int](
        spec=BatchSpec(max_size=3, max_wait_ms=1000),
        process_batch=process,
    )
    results = await asyncio.gather(*(engine.submit(i) for i in [1, 2, 3]))
    assert results == [2, 4, 6]
    # Single flush at size==3.
    assert flushes == [[1, 2, 3]]


@pytest.mark.asyncio
async def test_batching_flushes_after_timeout() -> None:
    flushes: list[list[int]] = []

    async def process(items: list[int]) -> list[int]:
        flushes.append(list(items))
        return [i + 100 for i in items]

    engine = BatchingEngine[int, int](
        spec=BatchSpec(max_size=10, max_wait_ms=20),
        process_batch=process,
    )
    # Only submit 2 items; size threshold won't fire — timer must.
    results = await asyncio.gather(engine.submit(7), engine.submit(8))
    assert results == [107, 108]
    assert flushes == [[7, 8]]


@pytest.mark.asyncio
async def test_batching_propagates_failure_to_all_callers() -> None:
    async def explode(_items: list[int]) -> list[int]:
        raise RuntimeError("backend down")

    engine = BatchingEngine[int, int](
        spec=BatchSpec(max_size=2, max_wait_ms=50),
        process_batch=explode,
    )
    with pytest.raises(RuntimeError):
        await asyncio.gather(engine.submit(1), engine.submit(2))


@pytest.mark.asyncio
async def test_batching_rejects_misaligned_results() -> None:
    async def shorter(items: list[int]) -> list[int]:
        return items[:-1]  # one short

    engine = BatchingEngine[int, int](
        spec=BatchSpec(max_size=2, max_wait_ms=50),
        process_batch=shorter,
    )
    with pytest.raises(ValueError):
        await asyncio.gather(engine.submit(1), engine.submit(2))


def test_batch_spec_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        BatchSpec(max_size=0, max_wait_ms=10)
    with pytest.raises(ValueError):
        BatchSpec(max_size=10, max_wait_ms=0)
