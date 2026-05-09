from __future__ import annotations

from dataclasses import dataclass

# These weights are MANDATED by the Phase-4 spec and by RETRIEVAL_SYSTEM_SPEC.md.
# They are not configurable at runtime — changing them changes the meaning of
# every score this system has ever produced. Bump SCHEMA_VERSION first if you
# really must adjust them.
SEMANTIC_WEIGHT: float = 0.35
GRAPH_WEIGHT: float = 0.25
RECENCY_WEIGHT: float = 0.20
IMPORTANCE_WEIGHT: float = 0.15
FEEDBACK_WEIGHT: float = 0.05


@dataclass(frozen=True, slots=True)
class FeatureWeights:
    """Immutable bundle of the five ranking weights.

    Encoded as a frozen dataclass so a future Phase 5+ can introduce
    learned weights via a different `FeatureWeights` instance without
    mutating the global constants.
    """

    semantic: float = SEMANTIC_WEIGHT
    graph: float = GRAPH_WEIGHT
    recency: float = RECENCY_WEIGHT
    importance: float = IMPORTANCE_WEIGHT
    feedback: float = FEEDBACK_WEIGHT

    def total(self) -> float:
        return self.semantic + self.graph + self.recency + self.importance + self.feedback

    def __post_init__(self) -> None:
        # Sanity: the mandated weights sum to 1.0; any caller passing
        # other weights at least gets a clear contract violation.
        if abs(self.total() - 1.0) > 1e-9:
            raise ValueError(
                f"FeatureWeights must sum to 1.0; got {self.total()}"
            )


FEATURE_WEIGHTS: FeatureWeights = FeatureWeights()
