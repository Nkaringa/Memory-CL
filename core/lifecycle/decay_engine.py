"""Decay engine — downgrade-only lifecycle policy.

Rules (from Phase-6 spec):
    If `no access for N days` AND
       `low graph_centrality` AND
       `low retrieval ranking`:
        compress summary, reduce embedding priority,
        mark as "low_priority_index", do NOT delete unless orphaned

We never delete. The strongest action is to set a Redis status flag
that the retrieval layer (or admins) can consult; embeddings + graph
edges stay intact.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from core.lifecycle.context import LifecycleContext
from core.lifecycle.logevent import emit_phase6_event
from core.lifecycle.relevance_scorer import RelevanceBreakdown
from core.observability import get_tracer

_tracer = get_tracer("core.lifecycle.decay_engine")


def _status_key(repo_id: str, entity_id: str) -> str:
    return f"phase6:status:{repo_id}:{entity_id}"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    LOW_PRIORITY_INDEX = "low_priority_index"


class DecayAction(StrEnum):
    NO_OP = "no_op"
    PROMOTE = "promote"          # active relevance → unset low priority
    DOWNGRADE = "downgrade"      # active → low_priority_index


@dataclass(frozen=True, slots=True)
class DecayDecision:
    entity_id: str
    action: DecayAction
    relevance_score: float
    reason: str


@dataclass(frozen=True, slots=True)
class DecayPlan:
    """Outcome of a single decay scan.

    Applying the plan is opt-in (`apply=True`); the plan itself is
    deterministic and re-runnable, so an operator can dry-run first
    and inspect before flipping the switch.
    """

    decisions: tuple[DecayDecision, ...]
    applied: bool

    @property
    def downgrades(self) -> int:
        return sum(1 for d in self.decisions if d.action == DecayAction.DOWNGRADE)

    @property
    def promotions(self) -> int:
        return sum(1 for d in self.decisions if d.action == DecayAction.PROMOTE)


@dataclass(frozen=True, slots=True)
class DecayPolicy:
    """Knobs threaded into the engine.

    Defaults match `Settings`'s lifecycle_* fields.
    """

    decay_threshold_days: int
    low_priority_threshold: float
    centrality_threshold: float


@dataclass(slots=True)
class _Inputs:
    """Per-entity inputs the engine operates on."""

    breakdown: RelevanceBreakdown
    last_access_at: datetime | None
    current_status: EntityStatus
    is_orphaned: bool = False


class DecayEngine:
    """Compute and (optionally) apply decay decisions."""

    def __init__(self, *, policy: DecayPolicy) -> None:
        self._policy = policy

    async def plan(
        self,
        ctx: LifecycleContext,
        *,
        entities: Sequence[_Inputs],
        apply: bool = False,
    ) -> DecayPlan:
        start = time.perf_counter()
        with _tracer.start_as_current_span("decay_engine.run") as span:
            span.set_attribute("repo_id", ctx.repo_id)
            span.set_attribute("entity_count", len(entities))

            decisions: list[DecayDecision] = []
            for inp in entities:
                decision = self._decide(inp, now=ctx.now)
                decisions.append(decision)

            # Determinism: sort by entity_id so the same input set
            # always yields the same plan ordering.
            decisions.sort(key=lambda d: d.entity_id)

            applied = False
            if apply:
                applied = await self._apply(ctx, decisions)

            elapsed = (time.perf_counter() - start) * 1000
            emit_phase6_event(
                event="decay_scan",
                entity_id="<batch>",
                operation="decay",
                relevance_score=0.0,
                status="success",
                level="info",
                latency_ms=round(elapsed, 3),
                decision_count=len(decisions),
                downgrades=sum(1 for d in decisions
                               if d.action == DecayAction.DOWNGRADE),
                promotions=sum(1 for d in decisions
                               if d.action == DecayAction.PROMOTE),
                applied=applied,
            )
            return DecayPlan(decisions=tuple(decisions), applied=applied)

    # ----- internals -----
    def _decide(self, inp: _Inputs, *, now: datetime) -> DecayDecision:
        score = inp.breakdown.score
        # Promotion path: an entity that was previously downgraded but
        # has bounced back gets `promote`. Orphaned entities never get
        # promoted (orphan = no graph anchor).
        if (
            inp.current_status == EntityStatus.LOW_PRIORITY_INDEX
            and score >= self._policy.low_priority_threshold
            and not inp.is_orphaned
        ):
            return DecayDecision(
                entity_id=inp.breakdown.entity_id,
                action=DecayAction.PROMOTE,
                relevance_score=score,
                reason=f"score {score:.3f} >= threshold "
                       f"{self._policy.low_priority_threshold:.3f}",
            )

        # Downgrade path requires ALL three conditions per spec.
        if inp.current_status == EntityStatus.ACTIVE:
            stale = self._is_stale(inp.last_access_at, now)
            low_centrality = (
                inp.breakdown.centrality < self._policy.centrality_threshold
            )
            low_priority = score < self._policy.low_priority_threshold
            if stale and low_centrality and low_priority:
                return DecayDecision(
                    entity_id=inp.breakdown.entity_id,
                    action=DecayAction.DOWNGRADE,
                    relevance_score=score,
                    reason=(
                        f"stale={stale}, centrality={inp.breakdown.centrality:.3f}, "
                        f"score={score:.3f}"
                    ),
                )

        return DecayDecision(
            entity_id=inp.breakdown.entity_id,
            action=DecayAction.NO_OP,
            relevance_score=score,
            reason="no decay condition triggered",
        )

    def _is_stale(self, last_access_at: datetime | None, now: datetime) -> bool:
        if last_access_at is None:
            return True
        cutoff = now - timedelta(days=self._policy.decay_threshold_days)
        return last_access_at < cutoff

    async def _apply(
        self, ctx: LifecycleContext, decisions: Sequence[DecayDecision]
    ) -> bool:
        """Apply decisions to Redis status keys.

        Pure soft-mutation — we only flip a single string per entity
        and emit an audit event. No graph or vector data is touched.
        """
        client = ctx.state.redis.client
        for d in decisions:
            if d.action == DecayAction.NO_OP:
                continue
            key = _status_key(ctx.repo_id, d.entity_id)
            if d.action == DecayAction.DOWNGRADE:
                await client.set(key, EntityStatus.LOW_PRIORITY_INDEX.value)
                emit_phase6_event(
                    event="memory_evolution",
                    entity_id=d.entity_id,
                    operation="decay",
                    relevance_score=d.relevance_score,
                    status="success",
                    reason=d.reason,
                )
            elif d.action == DecayAction.PROMOTE:
                await client.set(key, EntityStatus.ACTIVE.value)
                emit_phase6_event(
                    event="memory_evolution",
                    entity_id=d.entity_id,
                    operation="promote",
                    relevance_score=d.relevance_score,
                    status="success",
                    reason=d.reason,
                )
        return True


async def get_status(client: Any, repo_id: str, entity_id: str) -> EntityStatus:
    """Read accessor for the per-entity status flag."""
    raw = await client.get(_status_key(repo_id, entity_id))
    if raw == EntityStatus.LOW_PRIORITY_INDEX.value:
        return EntityStatus.LOW_PRIORITY_INDEX
    return EntityStatus.ACTIVE


# Re-exported under a friendlier name for orchestration.
DecayEngineInputs = _Inputs

__all__ = [
    "DecayAction",
    "DecayDecision",
    "DecayEngine",
    "DecayEngineInputs",
    "DecayPlan",
    "DecayPolicy",
    "EntityStatus",
    "get_status",
]
