"""Ingestion distributor — assigns repos to ingestion shards.

The distributor is a thin planner: given a batch of repos to ingest,
it produces a `DistributedIngestionPlan` mapping each repo to its
shard via the existing `GraphShardRouter`. Actual concurrent
execution is the job of `infra/distributed/worker_pool.py` —
keeping the planner pure makes it deterministic and trivially
testable.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from core.scaling.graph_shard_router import GraphShardRouter
from core.scaling.vector_shard_router import VectorShardRouter


@dataclass(frozen=True, slots=True)
class IngestionShardAssignment:
    repo_id: str
    repo_path: str
    commit_sha: str
    graph_shard_id: str
    vector_shard_id: str
    vector_collection: str


@dataclass(frozen=True, slots=True)
class DistributedIngestionPlan:
    assignments: tuple[IngestionShardAssignment, ...]

    @property
    def shard_distribution(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for a in self.assignments:
            counts[a.graph_shard_id] += 1
        return dict(sorted(counts.items()))


@dataclass(frozen=True, slots=True)
class _IngestRequest:
    repo_id: str
    repo_path: str
    commit_sha: str


class IngestionDistributor:
    """Plan-only distributor — no async, no I/O, fully deterministic."""

    def __init__(
        self,
        *,
        graph_router: GraphShardRouter,
        vector_router: VectorShardRouter,
    ) -> None:
        self._graph = graph_router
        self._vector = vector_router

    def plan(
        self, requests: Sequence[_IngestRequest],
    ) -> DistributedIngestionPlan:
        # Determinism: stable order by repo_id regardless of input order.
        ordered = sorted(set(requests), key=lambda r: r.repo_id)
        assignments: list[IngestionShardAssignment] = []
        for req in ordered:
            graph = self._graph.route(repo_id=req.repo_id)
            vector = self._vector.route(repo_id=req.repo_id)
            assignments.append(
                IngestionShardAssignment(
                    repo_id=req.repo_id,
                    repo_path=req.repo_path,
                    commit_sha=req.commit_sha,
                    graph_shard_id=graph.shard_id,
                    vector_shard_id=vector.shard_id,
                    vector_collection=vector.collection_name,
                )
            )
        return DistributedIngestionPlan(assignments=tuple(assignments))


# Friendly alias used by the API layer.
IngestRequest = _IngestRequest

__all__ = [
    "DistributedIngestionPlan",
    "IngestRequest",
    "IngestionDistributor",
    "IngestionShardAssignment",
]
