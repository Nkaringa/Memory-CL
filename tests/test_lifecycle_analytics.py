from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.analytics import (
    PerformanceAnalyzer,
    PerformanceSignals,
    RetrievalFeedbackCollector,
    UsageTracker,
)
from core.ranking.feature_weights import FEATURE_WEIGHTS


# ---- UsageTracker ---------------------------------------------------------
class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def incr(self, key: str) -> int:
        new = int(self.store.get(key, "0")) + 1
        self.store[key] = str(new)
        return new

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> None:
        self.store[key] = value

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]


@pytest.mark.asyncio
async def test_usage_tracker_records_access_and_reads_back() -> None:
    redis = _FakeRedis()
    tracker = UsageTracker(redis)
    when = datetime(2026, 1, 1, tzinfo=UTC)
    n = await tracker.record_access(repo_id="r", entity_id="u1", at=when)
    assert n == 1
    n2 = await tracker.record_access(repo_id="r", entity_id="u1", at=when)
    assert n2 == 2
    stats = await tracker.get_stats("r", "u1")
    assert stats.usage_count == 2
    assert stats.last_access_at == when


@pytest.mark.asyncio
async def test_usage_tracker_bulk_get_uses_mget() -> None:
    redis = _FakeRedis()
    tracker = UsageTracker(redis)
    await tracker.record_access(repo_id="r", entity_id="u1")
    await tracker.record_access(repo_id="r", entity_id="u2")
    bulk = await tracker.bulk_get_stats("r", ["u2", "u1", "u3"])
    assert bulk["u1"].usage_count == 1
    assert bulk["u2"].usage_count == 1
    assert bulk["u3"].usage_count == 0  # missing → zero, not raise


# ---- RetrievalFeedbackCollector ------------------------------------------
@pytest.mark.asyncio
async def test_feedback_collector_counts_attempts_and_successes() -> None:
    redis = _FakeRedis()
    fb = RetrievalFeedbackCollector(redis)
    await fb.record_outcome(repo_id="r", entity_id="u1", success=True)
    await fb.record_outcome(repo_id="r", entity_id="u1", success=False)
    o3 = await fb.record_outcome(repo_id="r", entity_id="u1", success=True)
    assert o3.attempts == 3
    assert o3.successes == 2
    assert pytest.approx(o3.success_rate, abs=1e-6) == 2 / 3


@pytest.mark.asyncio
async def test_feedback_collector_failure_does_not_bump_successes() -> None:
    redis = _FakeRedis()
    fb = RetrievalFeedbackCollector(redis)
    o = await fb.record_outcome(repo_id="r", entity_id="u1", success=False)
    assert o.attempts == 1
    assert o.successes == 0


# ---- PerformanceAnalyzer --------------------------------------------------
def test_analyzer_returns_baseline_when_no_signal() -> None:
    out = PerformanceAnalyzer().propose_weights(PerformanceSignals())
    assert out == FEATURE_WEIGHTS


def test_analyzer_drifts_toward_successful_channels() -> None:
    out = PerformanceAnalyzer(max_drift=0.1).propose_weights(
        PerformanceSignals(
            vector_success_rate=1.0,
            graph_success_rate=0.0,
            metadata_success_rate=0.5,
            feedback_volume=10,
        ),
    )
    # Vector success ↑ → semantic should rise vs baseline.
    assert out.semantic > FEATURE_WEIGHTS.semantic
    # Graph success ↓ → graph weight should fall.
    assert out.graph < FEATURE_WEIGHTS.graph


def test_analyzer_output_remains_normalized() -> None:
    out = PerformanceAnalyzer(max_drift=0.5).propose_weights(
        PerformanceSignals(
            vector_success_rate=0.9,
            graph_success_rate=0.1,
            metadata_success_rate=0.7,
            feedback_volume=100,
        ),
    )
    assert pytest.approx(out.total(), abs=1e-9) == 1.0


def test_analyzer_rejects_invalid_max_drift() -> None:
    with pytest.raises(ValueError):
        PerformanceAnalyzer(max_drift=0.6)
    with pytest.raises(ValueError):
        PerformanceAnalyzer(max_drift=-0.1)
