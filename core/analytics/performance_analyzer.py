"""Performance analyzer — produces normalized FeatureWeights from analytics.

Phase-4 weights are mandated. Phase 6 may PROPOSE new weights based
on observed signals; the produced `FeatureWeights` is consumable by
`RankingModel(weights=...)` without modifying Phase 4. The proposal
preserves the spec's two invariants:
    * weights stay in [0, 1]
    * weights sum to 1.0
"""

from __future__ import annotations

from dataclasses import dataclass

from core.ranking.feature_weights import FEATURE_WEIGHTS, FeatureWeights


@dataclass(frozen=True, slots=True)
class PerformanceSignals:
    """Aggregate analytics fed into the weight optimizer.

    Each rate is in [0, 1] and represents how well the corresponding
    feature has been doing — for example, `vector_success_rate` is the
    fraction of vector-channel hits that produced useful retrievals.
    """

    vector_success_rate: float = 0.5
    graph_success_rate: float = 0.5
    metadata_success_rate: float = 0.5
    feedback_volume: int = 0  # number of feedback events observed

    @property
    def has_signal(self) -> bool:
        """`feedback_volume == 0` means we should not yet adjust weights."""
        return self.feedback_volume > 0


class PerformanceAnalyzer:
    """Translate analytics into adjusted ranking weights.

    Algorithm:
        1. If signal is missing, return the mandated defaults verbatim.
        2. Otherwise blend channel success rates into the matching
           feature-weight slots, then renormalize to sum=1.0.

    The blend factor is bounded — `max_drift` caps the per-call shift
    so a single noisy signal cannot move weights catastrophically.
    Same inputs → same outputs (no PRNG, no clock).
    """

    def __init__(self, *, max_drift: float = 0.1) -> None:
        if not 0.0 <= max_drift <= 0.5:
            raise ValueError("max_drift must be in [0, 0.5]")
        self._max_drift = max_drift

    def propose_weights(
        self, signals: PerformanceSignals,
        *,
        baseline: FeatureWeights = FEATURE_WEIGHTS,
    ) -> FeatureWeights:
        if not signals.has_signal:
            return baseline

        # Centered drift: success_rate above 0.5 → positive shift.
        drift_semantic = (signals.vector_success_rate - 0.5) * self._max_drift
        drift_graph = (signals.graph_success_rate - 0.5) * self._max_drift
        # Metadata drift bumps the recency weight, since the metadata
        # channel is the recency signal source.
        drift_recency = (signals.metadata_success_rate - 0.5) * self._max_drift

        adjusted = {
            "semantic": max(0.0, baseline.semantic + drift_semantic),
            "graph": max(0.0, baseline.graph + drift_graph),
            "recency": max(0.0, baseline.recency + drift_recency),
            "importance": max(0.0, baseline.importance),
            "feedback": max(0.0, baseline.feedback),
        }
        total = sum(adjusted.values())
        if total <= 0:
            return baseline
        normalized = {k: v / total for k, v in adjusted.items()}
        return FeatureWeights(
            semantic=normalized["semantic"],
            graph=normalized["graph"],
            recency=normalized["recency"],
            importance=normalized["importance"],
            feedback=normalized["feedback"],
        )


__all__ = ["PerformanceAnalyzer", "PerformanceSignals"]
