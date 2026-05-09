"""Embedding drift detector.

Compares per-entity embedding vectors against a baseline snapshot
and reports cosine-shift severity. Phase 8 detects three drift
classes (per spec):

    embedding drift   — vector cosine shift over time
    retrieval drift   — ranking instability for the same query
    graph drift       — edge divergence across shards

Each is exposed as a `*Drift*` helper on the same detector so
callers consume one consistent surface.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class DriftSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Spec-mandated drift output."""

    severity: DriftSeverity
    affected_components: tuple[str, ...]
    suggested_action: str
    metric: str = ""
    sample_count: int = 0
    summary: dict[str, float] = field(default_factory=dict)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vector dimensions must match")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def _severity_for(max_shift: float) -> DriftSeverity:
    """Cosine-shift bands. 0.0 = identical, 2.0 = opposite (clipped to 2.0)."""
    if max_shift < 0.05:
        return DriftSeverity.LOW
    if max_shift < 0.20:
        return DriftSeverity.MEDIUM
    if max_shift < 0.50:
        return DriftSeverity.HIGH
    return DriftSeverity.CRITICAL


class EmbeddingDriftDetector:
    """Compares vector dictionaries between two snapshots.

    Two helpers cover the spec's other drift classes:
        * `analyze_ranking` — set-overlap drift over a ranked list
        * `analyze_graph_edges` — edge-set divergence across shards
    """

    def analyze_embeddings(
        self,
        *,
        baseline: Mapping[str, Sequence[float]],
        current: Mapping[str, Sequence[float]],
    ) -> DriftReport:
        common = sorted(set(baseline) & set(current))
        if not common:
            return DriftReport(
                severity=DriftSeverity.LOW,
                affected_components=(),
                suggested_action="no overlap with baseline; nothing to compare",
                metric="embedding",
                sample_count=0,
            )
        shifts: list[tuple[str, float]] = []
        for entity_id in common:
            cos = _cosine(baseline[entity_id], current[entity_id])
            # Drift = 1 - cos. Identical → 0, orthogonal → 1, opposite → 2.
            drift = 1.0 - cos
            shifts.append((entity_id, drift))

        shifts.sort(key=lambda t: -t[1])
        max_shift = shifts[0][1] if shifts else 0.0
        mean_shift = sum(s for _, s in shifts) / len(shifts)
        severity = _severity_for(max_shift)
        # Top-N affected components, deterministically chosen.
        top_affected = tuple(eid for eid, _ in shifts[:10])
        return DriftReport(
            severity=severity,
            affected_components=top_affected,
            suggested_action=_suggest_action(severity, "embeddings"),
            metric="embedding",
            sample_count=len(shifts),
            summary={
                "max_shift": round(max_shift, 6),
                "mean_shift": round(mean_shift, 6),
            },
        )

    def analyze_ranking(
        self,
        *,
        baseline_top_k: Sequence[str],
        current_top_k: Sequence[str],
    ) -> DriftReport:
        if not baseline_top_k:
            return DriftReport(
                severity=DriftSeverity.LOW,
                affected_components=(),
                suggested_action="empty baseline; nothing to compare",
                metric="ranking",
            )
        baseline_set = set(baseline_top_k)
        current_set = set(current_top_k)
        # Jaccard distance — 0 = identical sets, 1 = disjoint sets.
        intersection = baseline_set & current_set
        union = baseline_set | current_set
        if not union:
            jaccard = 0.0
        else:
            jaccard = 1.0 - (len(intersection) / len(union))
        # Map jaccard to severity: 0 → LOW, 1 → CRITICAL.
        severity = _severity_for(jaccard * 2)  # scale to align with cosine bands
        affected = tuple(sorted(baseline_set ^ current_set))
        return DriftReport(
            severity=severity,
            affected_components=affected[:10],
            suggested_action=_suggest_action(severity, "ranking"),
            metric="ranking",
            sample_count=len(union),
            summary={"jaccard_distance": round(jaccard, 6)},
        )

    def analyze_graph_edges(
        self,
        *,
        edges_per_shard: Mapping[str, Sequence[tuple[str, str, str]]],
    ) -> DriftReport:
        if len(edges_per_shard) < 2:
            return DriftReport(
                severity=DriftSeverity.LOW,
                affected_components=(),
                suggested_action="need >=2 shards to detect graph divergence",
                metric="graph",
            )
        shards = sorted(edges_per_shard)
        sets = {s: set(edges_per_shard[s]) for s in shards}
        # Compute symmetric difference across all shard pairs.
        total_diff: set[tuple[str, str, str]] = set()
        for i in range(len(shards)):
            for j in range(i + 1, len(shards)):
                total_diff |= sets[shards[i]] ^ sets[shards[j]]
        union_size = len(set().union(*sets.values()))
        ratio = (len(total_diff) / union_size) if union_size else 0.0
        severity = _severity_for(ratio * 2)
        return DriftReport(
            severity=severity,
            affected_components=tuple(shards),
            suggested_action=_suggest_action(severity, "graph"),
            metric="graph",
            sample_count=union_size,
            summary={"divergence_ratio": round(ratio, 6)},
        )


def _suggest_action(severity: DriftSeverity, kind: str) -> str:
    if severity == DriftSeverity.LOW:
        return f"{kind}: within tolerance — no action"
    if severity == DriftSeverity.MEDIUM:
        return f"{kind}: monitor; schedule a refresh on next idle cycle"
    if severity == DriftSeverity.HIGH:
        return f"{kind}: trigger Phase-6 refresh for affected entities"
    return f"{kind}: ESCALATE — full re-index recommended"


__all__ = [
    "DriftReport",
    "DriftSeverity",
    "EmbeddingDriftDetector",
]
