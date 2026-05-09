"""Top-level lifecycle state scanner.

Pulls usage + feedback signals from analytics, scores every supplied
entity, and runs the four planners in series. Output is a single
`LifecycleScanResult` carrying every plan plus the per-entity
relevance breakdowns. The scanner is *deterministic* given the same
state snapshot (LifecycleContext.now is held constant for the run).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass

from core.analytics import RetrievalFeedbackCollector, UsageTracker
from core.lifecycle.context import LifecycleContext
from core.lifecycle.decay_engine import (
    DecayEngine,
    DecayEngineInputs,
    DecayPlan,
    get_status,
)
from core.lifecycle.embedding_refresh_scheduler import (
    EmbeddingRefreshScheduler,
    RefreshPlan,
)
from core.lifecycle.graph_compactor import GraphCompactionPlan, GraphCompactor
from core.lifecycle.logevent import emit_phase6_event
from core.lifecycle.memory_compactor import CompactionPlan, MemoryCompactor
from core.lifecycle.relevance_scorer import (
    RelevanceBreakdown,
    RelevanceInputs,
    RelevanceScorer,
)
from core.observability import get_tracer
from schemas import GraphEdge, GraphNode, IngestionUnit

_tracer = get_tracer("core.lifecycle.state_scanner")


@dataclass(frozen=True, slots=True)
class LifecycleScanResult:
    breakdowns: dict[str, RelevanceBreakdown]
    decay: DecayPlan
    memory_compaction: CompactionPlan
    graph_compaction: GraphCompactionPlan
    refresh: RefreshPlan


class LifecycleStateScanner:
    """Orchestrate one full lifecycle pass deterministically."""

    def __init__(
        self,
        *,
        scorer: RelevanceScorer,
        decay_engine: DecayEngine,
        memory_compactor: MemoryCompactor,
        graph_compactor: GraphCompactor,
        refresh_scheduler: EmbeddingRefreshScheduler,
    ) -> None:
        self._scorer = scorer
        self._decay = decay_engine
        self._mem_compactor = memory_compactor
        self._graph_compactor = graph_compactor
        self._refresher = refresh_scheduler

    async def scan(
        self,
        ctx: LifecycleContext,
        *,
        units: Sequence[IngestionUnit],
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        previous_neighbor_signatures: dict[str, str] | None = None,
        apply_decay: bool = False,
    ) -> LifecycleScanResult:
        start = time.perf_counter()
        with _tracer.start_as_current_span("lifecycle.scan") as span:
            span.set_attribute("repo_id", ctx.repo_id)
            span.set_attribute("unit_count", len(units))
            span.set_attribute("node_count", len(nodes))

            # Step 1 — gather signals.
            usage = UsageTracker(ctx.state.redis.client)
            feedback = RetrievalFeedbackCollector(ctx.state.redis.client)
            entity_ids = sorted({n.node_id for n in nodes})
            usage_stats = await usage.bulk_get_stats(ctx.repo_id, entity_ids)
            feedback_stats = await feedback.bulk_get(ctx.repo_id, entity_ids)
            in_degree = self._compute_in_degree(nodes, edges)

            # Step 2 — score every entity.
            breakdowns: dict[str, RelevanceBreakdown] = {}
            for n in sorted(nodes, key=lambda x: x.node_id):
                u_stats = usage_stats.get(n.node_id)
                f_stats = feedback_stats.get(n.node_id)
                breakdown = self._scorer.score(
                    RelevanceInputs(
                        entity_id=n.node_id,
                        usage_count=u_stats.usage_count if u_stats else 0,
                        last_access_at=u_stats.last_access_at if u_stats else None,
                        graph_in_degree=in_degree.get(n.node_id, 0),
                        retrieval_attempts=f_stats.attempts if f_stats else 0,
                        retrieval_successes=f_stats.successes if f_stats else 0,
                    ),
                    now=ctx.now,
                )
                breakdowns[n.node_id] = breakdown

            # Step 3 — decay plan.
            decay_inputs: list[DecayEngineInputs] = []
            for n in sorted(nodes, key=lambda x: x.node_id):
                u_stats = usage_stats.get(n.node_id)
                current_status = await get_status(
                    ctx.state.redis.client, ctx.repo_id, n.node_id
                )
                decay_inputs.append(
                    DecayEngineInputs(
                        breakdown=breakdowns[n.node_id],
                        last_access_at=u_stats.last_access_at if u_stats else None,
                        current_status=current_status,
                        is_orphaned=False,  # orphan detection in Phase 7+
                    )
                )
            decay_plan = await self._decay.plan(
                ctx, entities=decay_inputs, apply=apply_decay,
            )

            # Step 4 — compaction plans (computed, not applied).
            mem_plan = self._mem_compactor.plan(units=units, scores=breakdowns)
            graph_plan = self._graph_compactor.plan(
                nodes=nodes, edges=edges, scores=breakdowns,
            )

            # Step 5 — embedding refresh plan with neighbor-signature drift.
            current_signatures = self._compute_neighbor_signatures(nodes, edges)
            refresh_plan = self._refresher.plan(
                scores=list(breakdowns.values()),
                previous_signatures=previous_neighbor_signatures or {},
                current_signatures=current_signatures,
            )

            elapsed = (time.perf_counter() - start) * 1000
            emit_phase6_event(
                event="memory_evolution",
                entity_id="<batch>",
                operation="scan",
                relevance_score=0.0,
                status="success",
                level="info",
                latency_ms=round(elapsed, 3),
                entities=len(breakdowns),
                downgrades=decay_plan.downgrades,
                promotions=decay_plan.promotions,
                modules_compacted=len(mem_plan.entries),
                graph_merges=len(graph_plan.merges),
                refresh_count=len(refresh_plan.decisions),
            )
            return LifecycleScanResult(
                breakdowns=breakdowns,
                decay=decay_plan,
                memory_compaction=mem_plan,
                graph_compaction=graph_plan,
                refresh=refresh_plan,
            )

    @staticmethod
    def _compute_in_degree(
        nodes: Sequence[GraphNode], edges: Sequence[GraphEdge],
    ) -> dict[str, int]:
        counts: dict[str, int] = {n.node_id: 0 for n in nodes}
        for e in edges:
            if e.dst_id in counts:
                counts[e.dst_id] += 1
        return counts

    @staticmethod
    def _compute_neighbor_signatures(
        nodes: Sequence[GraphNode], edges: Sequence[GraphEdge],
    ) -> dict[str, str]:
        """Per-node SHA-256 over its sorted outgoing neighbors.

        Same neighborhood → same signature; any change → different
        bytes. This is what `EmbeddingRefreshScheduler` uses to detect
        neighbor drift.
        """
        outgoing: dict[str, list[str]] = {n.node_id: [] for n in nodes}
        for e in edges:
            if e.src_id in outgoing:
                outgoing[e.src_id].append(e.dst_id)
        signatures: dict[str, str] = {}
        for nid, neighbors in outgoing.items():
            payload = ",".join(sorted(neighbors))
            signatures[nid] = hashlib.sha256(payload.encode()).hexdigest()
        return signatures


__all__ = ["LifecycleScanResult", "LifecycleStateScanner"]
