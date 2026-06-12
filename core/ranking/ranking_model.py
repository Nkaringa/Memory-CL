from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass

from core.observability import get_tracer
from core.ranking.feature_weights import FEATURE_WEIGHTS, FeatureWeights
from core.retrieval.logevent import emit_phase4_event
from schemas import (
    RankedResult,
    RankingFeatures,
    RetrievalCandidate,
    RetrievalChannel,
)

_tracer = get_tracer("core.ranking.ranking_model")


@dataclass(frozen=True, slots=True)
class RankingTrace:
    """Audit record for a single ranking computation.

    Tests use `breakdown == features` and `final_score == sum(w*f)` to
    detect drift in the formula without re-deriving the formula in the
    test suite.
    """

    unit_id: str
    final_score: float
    features: RankingFeatures
    weights: FeatureWeights


def _final_score(features: RankingFeatures, weights: FeatureWeights) -> float:
    """Apply the mandated ranking formula.

    FinalScore =
        0.35 * semantic + 0.25 * graph + 0.20 * recency
      + 0.15 * importance + 0.05 * feedback
    """
    score = (
        weights.semantic * features.semantic_similarity
        + weights.graph * features.graph_proximity
        + weights.recency * features.recency
        + weights.importance * features.importance
        + weights.feedback * features.user_feedback
    )
    # Clamp to [0, 1] in case of float drift; should never trigger.
    return max(0.0, min(1.0, score))


@dataclass(frozen=True, slots=True)
class CandidateProvenance:
    """Where a unit appeared, plus the per-channel raw scores.

    Built by `RankingModel` from the fused candidate list before
    scoring. Keeping it as a dataclass (rather than a tuple) makes the
    test assertions readable.
    """

    unit_id: str
    cosine: float | None
    graph_depth: int | None
    channels: tuple[RetrievalChannel, ...]
    file_path: str | None
    qualified_name: str | None
    kind: str | None
    # Best raw_score among METADATA hits for this unit (0.0 when the
    # unit never surfaced through the metadata channel). A lexical
    # exact-ish match is semantic evidence — without this, metadata-only
    # candidates ranked as exactly 0.0.
    metadata_score: float = 0.0


class RankingModel:
    """Apply the ranking formula to a set of fused retrieval candidates.

    Inputs:
        - candidates: every candidate produced by every channel
        - feature_provider: a callable that returns RankingFeatures for
          a given (unit_id, provenance) — keeps recency/importance pluggable
          (Phase 5 will wire real signals here).

    Output: deterministic `list[RankedResult]` sorted by:
        1. final_score DESC
        2. unit_id ASC (tie-break)
        3. file_path ASC (secondary tie-break, mandated by Phase 4 spec)
    """

    def __init__(self, *, weights: FeatureWeights | None = None) -> None:
        self._weights = weights or FEATURE_WEIGHTS

    def rank(
        self,
        candidates: Iterable[RetrievalCandidate],
        *,
        feature_provider: FeatureProvider | None = None,
        top_k: int | None = None,
        query_id: str = "",
        repo_id: str = "",
    ) -> list[RankedResult]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("ranking_engine.score") as span:
            span.set_attribute("query_id", query_id)
            span.set_attribute("repo_id", repo_id)

            grouped = self._group_by_unit(candidates)
            provider = feature_provider or _default_feature_provider

            ranked: list[RankedResult] = []
            for unit_id, prov in grouped.items():
                features = provider(unit_id, prov)
                score = _final_score(features, self._weights)
                ranked.append(
                    RankedResult(
                        unit_id=unit_id,
                        final_score=score,
                        breakdown=features,
                        channels=list(prov.channels),
                        file_path=prov.file_path,
                        qualified_name=prov.qualified_name,
                        kind=prov.kind,
                    )
                )

            # Deterministic sort: score DESC, unit_id ASC, file_path ASC.
            ranked.sort(
                key=lambda r: (
                    -r.final_score,
                    r.unit_id,
                    r.file_path or "",
                )
            )
            if top_k is not None:
                ranked = ranked[:top_k]

            span.set_attribute("count", len(ranked))
            emit_phase4_event(
                event="ranking_run",
                operation="rank",
                status="success",
                latency_ms=(time.perf_counter() - start) * 1000,
                query_id=query_id,
                repo_id=repo_id,
                level="debug",
                count=len(ranked),
            )
            return ranked

    @staticmethod
    def _group_by_unit(
        candidates: Iterable[RetrievalCandidate],
    ) -> dict[str, CandidateProvenance]:
        """Fuse multi-channel hits per unit_id, preserving deterministic order."""
        grouped: dict[str, dict[str, object]] = {}
        for c in candidates:
            slot = grouped.setdefault(
                c.unit_id,
                {
                    "cosine": None,
                    "graph_depth": None,
                    "metadata_score": 0.0,
                    "channels": set(),
                    "file_path": c.file_path,
                    "qualified_name": c.qualified_name,
                    "kind": c.kind,
                },
            )
            slot["channels"].add(c.channel)  # type: ignore[union-attr]
            if c.channel == RetrievalChannel.VECTOR and slot["cosine"] is None:
                slot["cosine"] = c.raw_score
            if c.channel == RetrievalChannel.GRAPH:
                # raw_score is graph proximity (1/(1+depth)); depth is
                # carried explicitly via `extra["depth"]` by the retriever.
                depth = c.extra.get("depth")
                if isinstance(depth, int):
                    if slot["graph_depth"] is None or depth < slot["graph_depth"]:  # type: ignore[operator]
                        slot["graph_depth"] = depth
            if c.channel == RetrievalChannel.METADATA:
                # Best metadata evidence wins when a unit matched several
                # metadata queries.
                slot["metadata_score"] = max(slot["metadata_score"], c.raw_score)  # type: ignore[call-overload]
            # Prefer richer provenance from any channel that filled in details.
            for key in ("file_path", "qualified_name", "kind"):
                if slot[key] is None:
                    slot[key] = getattr(c, key)

        out: dict[str, CandidateProvenance] = {}
        for unit_id in sorted(grouped):
            slot = grouped[unit_id]
            out[unit_id] = CandidateProvenance(
                unit_id=unit_id,
                cosine=slot["cosine"],  # type: ignore[arg-type]
                graph_depth=slot["graph_depth"],  # type: ignore[arg-type]
                channels=tuple(sorted(slot["channels"], key=lambda c: c.value)),  # type: ignore[arg-type]
                file_path=slot["file_path"],  # type: ignore[arg-type]
                qualified_name=slot["qualified_name"],  # type: ignore[arg-type]
                kind=slot["kind"],  # type: ignore[arg-type]
                metadata_score=slot["metadata_score"],  # type: ignore[arg-type]
            )
        return out


# A `FeatureProvider` returns the five ranking features for one unit.
# Phase 4 ships a default that derives features from provenance only;
# Phase 5 will replace it with one that consults Postgres for recency
# and Neo4j for graph in-degree.
FeatureProvider = "callable[[str, CandidateProvenance], RankingFeatures]"  # type alias hint


def _default_feature_provider(
    _unit_id: str, prov: CandidateProvenance
) -> RankingFeatures:
    """Channel-only feature derivation.

    Used by tests + by the API endpoint until Phase 5 wires per-feature
    repositories. Recency / importance / feedback default to 0 unless
    a richer provider is plugged in.
    """
    from core.ranking.scoring import (
        cosine_to_similarity,
        graph_proximity_from_depth,
    )

    cosine_sim = cosine_to_similarity(prov.cosine) if prov.cosine is not None else 0.0
    # Max-fusion: a metadata hit is a lexical exact-ish match on the
    # symbol/docstring — that IS semantic evidence. Taking the stronger
    # of the two signals keeps the mandated five-feature formula intact
    # while ensuring metadata-only candidates score above 0.
    semantic = max(cosine_sim, prov.metadata_score)
    graph = (
        graph_proximity_from_depth(prov.graph_depth)
        if prov.graph_depth is not None
        else 0.0
    )
    return RankingFeatures(
        semantic_similarity=semantic,
        graph_proximity=graph,
        recency=0.0,
        importance=0.0,
        user_feedback=0.0,
    )


__all__ = [
    "CandidateProvenance",
    "FeatureProvider",
    "RankingModel",
    "RankingTrace",
]
