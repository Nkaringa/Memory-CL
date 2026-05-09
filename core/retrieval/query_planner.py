from __future__ import annotations

import time
from dataclasses import dataclass

from core.observability import get_tracer
from core.retrieval.logevent import emit_phase4_event
from schemas import Query

_tracer = get_tracer("core.retrieval.query_planner")


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Concrete plan describing which channels to invoke and how."""

    use_vector: bool
    use_graph: bool
    use_metadata: bool
    vector_top_k: int
    graph_seeds: tuple[str, ...]
    graph_max_depth: int
    metadata_top_k: int


class QueryPlanner:
    """Translate a `Query` into a `QueryPlan`.

    Phase-4 ships a simple deterministic planner: vector + metadata are
    always on; graph is on iff the caller supplied seed unit_ids.
    Future phases can introduce learned planners behind this surface
    without touching downstream retrievers.
    """

    def __init__(self, *, default_max_depth: int = 3) -> None:
        if default_max_depth <= 0:
            raise ValueError("default_max_depth must be > 0")
        self._default_max_depth = default_max_depth

    def plan(self, query: Query) -> QueryPlan:
        start = time.perf_counter()
        with _tracer.start_as_current_span("query_planner.run") as span:
            seeds = tuple(query.seed_unit_ids)
            plan = QueryPlan(
                use_vector=True,
                use_metadata=True,
                use_graph=bool(seeds),
                vector_top_k=query.top_k,
                metadata_top_k=query.top_k,
                graph_seeds=seeds,
                graph_max_depth=self._default_max_depth,
            )
            span.set_attribute("use_vector", plan.use_vector)
            span.set_attribute("use_graph", plan.use_graph)
            span.set_attribute("use_metadata", plan.use_metadata)
            emit_phase4_event(
                event="plan_built",
                operation="plan",
                status="success",
                latency_ms=(time.perf_counter() - start) * 1000,
                query_id="",
                repo_id=query.repo_id,
                level="debug",
                use_vector=plan.use_vector,
                use_graph=plan.use_graph,
                use_metadata=plan.use_metadata,
            )
            return plan


__all__ = ["QueryPlan", "QueryPlanner"]
