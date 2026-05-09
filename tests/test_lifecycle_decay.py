from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.lifecycle import (
    DecayAction,
    DecayEngine,
    DecayEngineInputs,
    DecayPolicy,
    EntityStatus,
    LifecycleContext,
    RelevanceBreakdown,
    get_status,
)


def _bd(eid: str, score: float, *, centrality: float = 0.0) -> RelevanceBreakdown:
    return RelevanceBreakdown(
        entity_id=eid, score=score, usage=0.0, recency=0.0,
        centrality=centrality, success=0.0,
    )


def _policy(**overrides):
    base = {
        "decay_threshold_days": 30,
        "low_priority_threshold": 0.3,
        "centrality_threshold": 0.2,
    }
    base.update(overrides)
    return DecayPolicy(**base)


def _ctx(redis_client) -> LifecycleContext:
    state = SimpleNamespace(redis=SimpleNamespace(client=redis_client))
    return LifecycleContext(repo_id="r", state=state, now=datetime(2026, 6, 1, tzinfo=UTC))


@pytest.mark.asyncio
async def test_no_op_for_active_high_relevance() -> None:
    inp = DecayEngineInputs(
        breakdown=_bd("u", 0.8, centrality=0.5),
        last_access_at=datetime(2026, 5, 1, tzinfo=UTC),
        current_status=EntityStatus.ACTIVE,
    )
    plan = await DecayEngine(policy=_policy()).plan(_ctx(AsyncMock()), entities=[inp])
    assert plan.decisions[0].action == DecayAction.NO_OP


@pytest.mark.asyncio
async def test_downgrade_only_when_all_three_conditions_met() -> None:
    # Stale (180 days old), low centrality, low score.
    stale = datetime(2025, 12, 1, tzinfo=UTC)
    inp = DecayEngineInputs(
        breakdown=_bd("u", 0.1, centrality=0.05),
        last_access_at=stale,
        current_status=EntityStatus.ACTIVE,
    )
    plan = await DecayEngine(policy=_policy()).plan(_ctx(AsyncMock()), entities=[inp])
    assert plan.decisions[0].action == DecayAction.DOWNGRADE


@pytest.mark.asyncio
async def test_no_downgrade_when_centrality_high() -> None:
    inp = DecayEngineInputs(
        breakdown=_bd("u", 0.1, centrality=0.9),  # highly central
        last_access_at=datetime(2025, 12, 1, tzinfo=UTC),
        current_status=EntityStatus.ACTIVE,
    )
    plan = await DecayEngine(policy=_policy()).plan(_ctx(AsyncMock()), entities=[inp])
    assert plan.decisions[0].action == DecayAction.NO_OP


@pytest.mark.asyncio
async def test_promote_when_score_recovers_above_threshold() -> None:
    inp = DecayEngineInputs(
        breakdown=_bd("u", 0.7, centrality=0.5),
        last_access_at=datetime(2026, 5, 1, tzinfo=UTC),
        current_status=EntityStatus.LOW_PRIORITY_INDEX,
    )
    plan = await DecayEngine(policy=_policy()).plan(_ctx(AsyncMock()), entities=[inp])
    assert plan.decisions[0].action == DecayAction.PROMOTE


@pytest.mark.asyncio
async def test_apply_writes_redis_status_flag() -> None:
    redis = AsyncMock()
    inp = DecayEngineInputs(
        breakdown=_bd("u", 0.1, centrality=0.05),
        last_access_at=datetime(2025, 12, 1, tzinfo=UTC),
        current_status=EntityStatus.ACTIVE,
    )
    plan = await DecayEngine(policy=_policy()).plan(
        _ctx(redis), entities=[inp], apply=True,
    )
    assert plan.applied is True
    redis.set.assert_awaited_once()
    args = redis.set.call_args.args
    assert args[0] == "phase6:status:r:u"
    assert args[1] == "low_priority_index"


@pytest.mark.asyncio
async def test_plan_is_deterministic_for_unsorted_input() -> None:
    inputs = [
        DecayEngineInputs(
            breakdown=_bd(f"u-{c}", 0.1, centrality=0.0),
            last_access_at=datetime(2025, 1, 1, tzinfo=UTC),
            current_status=EntityStatus.ACTIVE,
        )
        for c in "zab"
    ]
    a = await DecayEngine(policy=_policy()).plan(_ctx(AsyncMock()), entities=inputs)
    b = await DecayEngine(policy=_policy()).plan(
        _ctx(AsyncMock()), entities=list(reversed(inputs)),
    )
    assert [d.entity_id for d in a.decisions] == [d.entity_id for d in b.decisions]


@pytest.mark.asyncio
async def test_get_status_reads_back_active_default() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    status = await get_status(redis, "r", "u")
    assert status == EntityStatus.ACTIVE

    redis.get = AsyncMock(return_value="low_priority_index")
    assert await get_status(redis, "r", "u") == EntityStatus.LOW_PRIORITY_INDEX
