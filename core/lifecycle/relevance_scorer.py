from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime

from core.lifecycle.logevent import emit_phase6_event
from core.observability import get_tracer

_tracer = get_tracer("core.lifecycle.relevance_scorer")


# Mandated weights — DO NOT change without bumping the lifecycle schema.
USAGE_WEIGHT: float = 0.4
RECENCY_WEIGHT: float = 0.3
CENTRALITY_WEIGHT: float = 0.2
SUCCESS_WEIGHT: float = 0.1


@dataclass(frozen=True, slots=True)
class RelevanceInputs:
    """Per-entity observables fed into the relevance formula.

    `usage_count`         — calls / accesses in the analytics window
    `last_access_at`      — None means never accessed
    `graph_in_degree`     — incoming edges (centrality proxy)
    `retrieval_attempts`  — number of retrieval calls returning this entity
    `retrieval_successes` — successful outcomes (per feedback collector)
    """

    entity_id: str
    usage_count: int = 0
    last_access_at: datetime | None = None
    graph_in_degree: int = 0
    retrieval_attempts: int = 0
    retrieval_successes: int = 0


def _usage_score(count: int, *, saturate_at: int) -> float:
    """Saturating sqrt curve: 0 → 0, saturate_at+ → 1."""
    if count <= 0 or saturate_at <= 0:
        return 0.0
    if count >= saturate_at:
        return 1.0
    return math.sqrt(count / saturate_at)


def _recency_score(
    last_access_at: datetime | None, now: datetime, *, half_life_days: float,
) -> float:
    """Exponential decay: 1.0 at access time, halves every `half_life_days`."""
    if last_access_at is None:
        return 0.0
    age_seconds = (now - last_access_at).total_seconds()
    if age_seconds <= 0:
        return 1.0
    if half_life_days <= 0:
        return 0.0
    age_days = age_seconds / 86400
    return 0.5 ** (age_days / half_life_days)


def _centrality_score(in_degree: int, *, saturate_at: int) -> float:
    if in_degree <= 0 or saturate_at <= 0:
        return 0.0
    if in_degree >= saturate_at:
        return 1.0
    return math.sqrt(in_degree / saturate_at)


def _success_rate(attempts: int, successes: int) -> float:
    if attempts <= 0:
        return 0.0
    rate = successes / attempts
    return max(0.0, min(1.0, rate))


@dataclass(frozen=True, slots=True)
class RelevanceBreakdown:
    """Audit record per scored entity."""

    entity_id: str
    score: float
    usage: float
    recency: float
    centrality: float
    success: float


class RelevanceScorer:
    """Compute the mandated RelevanceScore overlay.

    Formula:
        0.4 usage + 0.3 recency + 0.2 graph_centrality + 0.1 success_rate

    Pure function of inputs — same `RelevanceInputs` + same `now`
    always produce the same score. The class wrapper exists so the
    saturation knobs are configurable per deployment without
    repeatedly threading them through every call site.
    """

    def __init__(
        self,
        *,
        usage_window_days: int = 14,
        usage_saturate_at: int = 32,
        centrality_saturate_at: int = 16,
    ) -> None:
        if usage_window_days <= 0:
            raise ValueError("usage_window_days must be > 0")
        self._usage_window_days = usage_window_days
        self._usage_saturate_at = usage_saturate_at
        self._centrality_saturate_at = centrality_saturate_at

    def score(
        self, inputs: RelevanceInputs, *, now: datetime
    ) -> RelevanceBreakdown:
        start = time.perf_counter()
        with _tracer.start_as_current_span("relevance_scorer.compute") as span:
            span.set_attribute("entity_id", inputs.entity_id)

            usage = _usage_score(
                inputs.usage_count, saturate_at=self._usage_saturate_at
            )
            recency = _recency_score(
                inputs.last_access_at, now,
                half_life_days=float(self._usage_window_days),
            )
            centrality = _centrality_score(
                inputs.graph_in_degree, saturate_at=self._centrality_saturate_at
            )
            success = _success_rate(
                inputs.retrieval_attempts, inputs.retrieval_successes
            )

            score = (
                USAGE_WEIGHT * usage
                + RECENCY_WEIGHT * recency
                + CENTRALITY_WEIGHT * centrality
                + SUCCESS_WEIGHT * success
            )
            score = max(0.0, min(1.0, score))

            elapsed = (time.perf_counter() - start) * 1000
            emit_phase6_event(
                event="relevance_scored",
                entity_id=inputs.entity_id,
                operation="scan",
                relevance_score=score,
                status="success",
                level="debug",
                latency_ms=round(elapsed, 3),
                usage=round(usage, 6),
                recency=round(recency, 6),
                centrality=round(centrality, 6),
                success=round(success, 6),
            )
            return RelevanceBreakdown(
                entity_id=inputs.entity_id,
                score=score,
                usage=usage,
                recency=recency,
                centrality=centrality,
                success=success,
            )


__all__ = [
    "CENTRALITY_WEIGHT",
    "RECENCY_WEIGHT",
    "SUCCESS_WEIGHT",
    "USAGE_WEIGHT",
    "RelevanceBreakdown",
    "RelevanceInputs",
    "RelevanceScorer",
]
