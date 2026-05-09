"""Backpressure controller — 4-level throttle policy.

Spec mandates the throttle order:
    Level 1 → throttle ingestion first
    Level 2 → then retrieval fan-out
    Level 3 → then MCP tool execution
    Level 0 → no throttle
    Graph integrity is NEVER sacrificed (no level can throttle the
    graph layer).

The controller is fed `update(...)` snapshots of queue depth /
in-flight counts and reports the appropriate level. Same inputs
always produce the same level — no rolling state, just a pure
function of the current observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ThrottleLevel(IntEnum):
    """Mandated escalation order. Larger values throttle MORE layers."""

    NONE = 0
    INGESTION = 1
    INGESTION_AND_RETRIEVAL = 2
    INGESTION_AND_RETRIEVAL_AND_MCP = 3


@dataclass(frozen=True, slots=True)
class BackpressureSnapshot:
    level: ThrottleLevel
    queue_depth: int
    queue_capacity: int
    inflight: int
    inflight_capacity: int
    triggers: tuple[str, ...]

    @property
    def degraded(self) -> bool:
        return self.level != ThrottleLevel.NONE


class BackpressureController:
    """Stateless evaluator using two configured thresholds.

    A high `queue_ratio` (queue_depth / queue_capacity) escalates more
    aggressively than a high `inflight_ratio`. Two separate ratios
    make the policy easier to reason about than a single scalar.
    """

    def __init__(
        self,
        *,
        queue_threshold: float = 0.8,
        inflight_threshold: float = 0.9,
    ) -> None:
        if not 0.0 <= queue_threshold <= 1.0:
            raise ValueError("queue_threshold must be in [0, 1]")
        if not 0.0 <= inflight_threshold <= 1.0:
            raise ValueError("inflight_threshold must be in [0, 1]")
        self._queue_threshold = queue_threshold
        self._inflight_threshold = inflight_threshold

    def evaluate(
        self,
        *,
        queue_depth: int,
        queue_capacity: int,
        inflight: int,
        inflight_capacity: int,
    ) -> BackpressureSnapshot:
        if queue_capacity <= 0 or inflight_capacity <= 0:
            raise ValueError("capacities must be > 0")
        q_ratio = queue_depth / queue_capacity
        i_ratio = inflight / inflight_capacity
        triggers: list[str] = []

        # Determine level per the mandated escalation table.
        # Beyond 1.5x the threshold we escalate one more level; beyond
        # 2x the threshold we escalate to the highest level.
        level = ThrottleLevel.NONE
        if q_ratio >= self._queue_threshold or i_ratio >= self._inflight_threshold:
            level = ThrottleLevel.INGESTION
            triggers.append(
                f"q_ratio={q_ratio:.3f}>={self._queue_threshold}"
                if q_ratio >= self._queue_threshold else
                f"i_ratio={i_ratio:.3f}>={self._inflight_threshold}"
            )
        if q_ratio >= 1.5 * self._queue_threshold or i_ratio >= 1.5 * self._inflight_threshold:
            level = ThrottleLevel.INGESTION_AND_RETRIEVAL
            triggers.append("escalation:retrieval")
        if q_ratio >= 2.0 * self._queue_threshold or i_ratio >= 2.0 * self._inflight_threshold:
            level = ThrottleLevel.INGESTION_AND_RETRIEVAL_AND_MCP
            triggers.append("escalation:mcp")

        return BackpressureSnapshot(
            level=level,
            queue_depth=queue_depth,
            queue_capacity=queue_capacity,
            inflight=inflight,
            inflight_capacity=inflight_capacity,
            triggers=tuple(triggers),
        )

    @staticmethod
    def should_throttle(level: ThrottleLevel, layer: str) -> bool:
        """True iff the named layer is throttled at this level.

        Graph layer is NEVER throttled regardless of level — preserves
        the spec's "NEVER drop graph integrity" invariant.
        """
        if layer == "graph":
            return False
        if layer == "ingestion" and level >= ThrottleLevel.INGESTION:
            return True
        if layer == "retrieval" and level >= ThrottleLevel.INGESTION_AND_RETRIEVAL:
            return True
        return bool(
            layer == "mcp" and level >= ThrottleLevel.INGESTION_AND_RETRIEVAL_AND_MCP
        )


__all__ = ["BackpressureController", "BackpressureSnapshot", "ThrottleLevel"]
