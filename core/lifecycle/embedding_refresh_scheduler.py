"""Embedding refresh scheduler — produces a RefreshPlan.

Trigger conditions per spec:
    * relevance drops below `refresh_threshold`
    * graph neighbors change significantly (delta >= sensitivity)
    * retrieval failure rate increases (success_rate < 0.5)

Applying the plan re-embeds the affected units via the existing
Phase-3 EmbeddingPipeline. Scheduling alone is in Phase-6 scope; the
APPLY path is exposed but optional so callers can dry-run safely.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from core.lifecycle.logevent import emit_phase6_event
from core.lifecycle.relevance_scorer import RelevanceBreakdown
from core.observability import get_tracer

_tracer = get_tracer("core.lifecycle.embedding_refresh")


class RefreshReason(StrEnum):
    LOW_RELEVANCE = "low_relevance"
    NEIGHBOR_DRIFT = "neighbor_drift"
    LOW_SUCCESS_RATE = "low_success_rate"


@dataclass(frozen=True, slots=True)
class RefreshDecision:
    entity_id: str
    relevance_score: float
    reasons: tuple[RefreshReason, ...]


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    decisions: tuple[RefreshDecision, ...]

    @property
    def to_refresh(self) -> tuple[str, ...]:
        return tuple(d.entity_id for d in self.decisions)


@dataclass(frozen=True, slots=True)
class NeighborSnapshot:
    """Per-entity neighborhood signature for drift detection.

    `signature` is a deterministic hash of the sorted neighbor ids.
    Snapshots are produced by the caller (state scanner) and threaded
    in so refresh decisions stay testable in isolation.
    """

    entity_id: str
    signature: str


class EmbeddingRefreshScheduler:
    """Decide which entities should be re-embedded.

    Pure planner — does not call the embedding pipeline directly.
    Apply hooks live in the state scanner which can wire the plan
    back through Phase-3.
    """

    def __init__(
        self,
        *,
        refresh_threshold: float,
        success_rate_floor: float = 0.5,
    ) -> None:
        if not 0.0 <= refresh_threshold <= 1.0:
            raise ValueError("refresh_threshold must be in [0, 1]")
        self._threshold = refresh_threshold
        self._success_floor = success_rate_floor

    def plan(
        self,
        *,
        scores: Sequence[RelevanceBreakdown],
        previous_signatures: dict[str, str],
        current_signatures: dict[str, str],
    ) -> RefreshPlan:
        start = time.perf_counter()
        with _tracer.start_as_current_span("embedding_refresh.trigger") as span:
            span.set_attribute("scored_count", len(scores))

            decisions: list[RefreshDecision] = []
            for breakdown in scores:
                reasons: list[RefreshReason] = []
                if breakdown.score < self._threshold:
                    reasons.append(RefreshReason.LOW_RELEVANCE)

                prev = previous_signatures.get(breakdown.entity_id)
                cur = current_signatures.get(breakdown.entity_id)
                if prev is not None and cur is not None and prev != cur:
                    reasons.append(RefreshReason.NEIGHBOR_DRIFT)

                if breakdown.success < self._success_floor:
                    reasons.append(RefreshReason.LOW_SUCCESS_RATE)

                if reasons:
                    decisions.append(
                        RefreshDecision(
                            entity_id=breakdown.entity_id,
                            relevance_score=breakdown.score,
                            reasons=tuple(
                                sorted(reasons, key=lambda r: r.value)
                            ),
                        )
                    )

            decisions.sort(key=lambda d: d.entity_id)
            elapsed = (time.perf_counter() - start) * 1000
            emit_phase6_event(
                event="memory_evolution",
                entity_id="<batch>",
                operation="refresh",
                relevance_score=0.0,
                status="success",
                level="info",
                latency_ms=round(elapsed, 3),
                refresh_count=len(decisions),
            )
            return RefreshPlan(decisions=tuple(decisions))


__all__ = [
    "EmbeddingRefreshScheduler",
    "NeighborSnapshot",
    "RefreshDecision",
    "RefreshPlan",
    "RefreshReason",
]
