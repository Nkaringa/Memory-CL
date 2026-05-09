from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass

from core.observability import get_tracer
from core.retrieval.graph_retriever import GraphRetriever
from core.retrieval.logevent import emit_phase4_event
from core.retrieval.metadata_retriever import MetadataRetriever
from core.retrieval.query_planner import QueryPlan, QueryPlanner
from core.retrieval.vector_retriever import VectorRetriever
from schemas import Query, RetrievalCandidate

_tracer = get_tracer("core.retrieval.hybrid_retriever")


@dataclass(frozen=True, slots=True)
class HybridRetrievalResult:
    """Aggregated channel output before ranking.

    Per-channel hit counts are materialized so the API endpoint can
    report them and the structured-log emitter can include them.
    """

    candidates: tuple[RetrievalCandidate, ...]
    graph_hits: int
    vector_hits: int
    metadata_hits: int
    failed_channels: tuple[str, ...]
    latency_ms: float


class HybridRetriever:
    """Run all enabled retrieval channels in parallel, fuse the output.

    Failure-isolation per spec: a single channel raising does NOT abort
    the run — its candidates are dropped, the channel is recorded under
    `failed_channels`, and the remaining channels still contribute.
    """

    def __init__(
        self,
        *,
        planner: QueryPlanner,
        graph: GraphRetriever | None,
        vector: VectorRetriever | None,
        metadata: MetadataRetriever | None,
    ) -> None:
        self._planner = planner
        self._graph = graph
        self._vector = vector
        self._metadata = metadata

    async def run(
        self, query: Query, *, query_id: str
    ) -> HybridRetrievalResult:
        start = time.perf_counter()
        plan = self._planner.plan(query)

        with _tracer.start_as_current_span("hybrid_retriever.run") as span:
            span.set_attribute("query_id", query_id)
            span.set_attribute("repo_id", query.repo_id)
            span.set_attribute("plan.use_vector", plan.use_vector)
            span.set_attribute("plan.use_graph", plan.use_graph)
            span.set_attribute("plan.use_metadata", plan.use_metadata)

            tasks: list[tuple[str, asyncio.Task[Sequence[RetrievalCandidate]]]] = []

            if plan.use_vector and self._vector is not None:
                tasks.append((
                    "vector",
                    asyncio.create_task(self._vector.search(
                        query.text,
                        top_k=plan.vector_top_k,
                        unit_kinds=query.unit_kinds,
                        query_id=query_id,
                        repo_id=query.repo_id,
                    )),
                ))
            if plan.use_graph and self._graph is not None and plan.graph_seeds:
                tasks.append((
                    "graph",
                    asyncio.create_task(self._graph.search(
                        plan.graph_seeds,
                        query_id=query_id,
                        repo_id=query.repo_id,
                    )),
                ))
            if plan.use_metadata and self._metadata is not None:
                tasks.append((
                    "metadata",
                    asyncio.create_task(self._metadata.search(
                        query.text,
                        query.repo_id,
                        top_k=plan.metadata_top_k,
                        unit_kinds=query.unit_kinds,
                        query_id=query_id,
                    )),
                ))

            results = await asyncio.gather(
                *(t for _, t in tasks), return_exceptions=True
            )

            candidates: list[RetrievalCandidate] = []
            failed: list[str] = []
            channel_hits: dict[str, int] = {"graph": 0, "vector": 0, "metadata": 0}
            for (name, _task), res in zip(tasks, results, strict=True):
                if isinstance(res, BaseException):
                    failed.append(name)
                    continue
                channel_hits[name] = len(res)
                candidates.extend(res)

            # Determinism: stable secondary sort key. Ranking is the
            # final word on order, but the candidate list itself is
            # passed to logging and to assemblers, so we sort here.
            candidates.sort(key=lambda c: (c.channel.value, c.unit_id))

            elapsed = (time.perf_counter() - start) * 1000
            status = "degraded" if failed else "success"
            emit_phase4_event(
                event="retrieval_run",
                operation="retrieve",
                status=status,
                latency_ms=elapsed,
                query_id=query_id,
                repo_id=query.repo_id,
                level="info",
                graph_hits=channel_hits["graph"],
                vector_hits=channel_hits["vector"],
                metadata_hits=channel_hits["metadata"],
                final_candidates=len(candidates),
                failed_channels=sorted(failed),
                query=query.text[:120],
            )

            span.set_attribute("graph_hits", channel_hits["graph"])
            span.set_attribute("vector_hits", channel_hits["vector"])
            span.set_attribute("metadata_hits", channel_hits["metadata"])
            return HybridRetrievalResult(
                candidates=tuple(candidates),
                graph_hits=channel_hits["graph"],
                vector_hits=channel_hits["vector"],
                metadata_hits=channel_hits["metadata"],
                failed_channels=tuple(sorted(failed)),
                latency_ms=elapsed,
            )


__all__ = [
    "HybridRetrievalResult",
    "HybridRetriever",
    "QueryPlan",
]
