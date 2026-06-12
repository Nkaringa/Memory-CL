from __future__ import annotations

import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from core.observability import get_tracer
from core.retrieval.logevent import emit_phase4_event
from schemas import GraphNode, NodeKind, RetrievalCandidate, RetrievalChannel

_tracer = get_tracer("core.retrieval.graph_retriever")


class GraphTraversalSource(Protocol):
    """Subset of GraphRepository the retriever needs.

    Spelling out the dependency as a protocol (rather than the full
    GraphRepository) keeps tests free of every method we don't use,
    and documents that this retriever is read-only.
    """

    async def neighbors(
        self,
        node_id: str,
        edge_kinds: Sequence[str] | None = None,
        depth: int = 1,
    ) -> Sequence[GraphNode]: ...


@dataclass(frozen=True, slots=True)
class GraphHit:
    node_id: str
    depth: int
    proximity: float
    file_path: str | None
    qualified_name: str | None
    kind: str | None


class GraphRetriever:
    """Bounded-depth BFS over the project graph.

    Inputs: a list of seed `node_id`s and a max depth. Output:
    `RetrievalCandidate`s keyed by `unit_id == node_id` with
    `raw_score = graph_proximity_from_depth(depth)` — i.e. 1/(1+depth).

    EXTERNAL nodes are skipped per the Phase-4 spec (lowest priority,
    no internal structure to retrieve).
    """

    name: str = "graph_retriever"

    def __init__(self, source: GraphTraversalSource, *, max_depth: int) -> None:
        if max_depth <= 0:
            raise ValueError("max_depth must be > 0")
        self._source = source
        self._max_depth = max_depth

    async def search(
        self,
        seeds: Sequence[str],
        *,
        edge_kinds: Sequence[str] | None = None,
        query_id: str = "",
        repo_id: str = "",
    ) -> list[RetrievalCandidate]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("graph_retriever.search") as span:
            span.set_attribute("query_id", query_id)
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("seed_count", len(seeds))
            span.set_attribute("max_depth", self._max_depth)

            visited: dict[str, GraphHit] = {}
            # Each queue entry carries the discovered GraphNode (or None
            # for seeds that couldn't be hydrated) so visit-on-pop has
            # access to the metadata. Seeds are hydrated via the source's
            # optional `get_node` so depth-0 candidates carry
            # qualified_name/kind/file_path instead of nulls; sources
            # without `get_node` (e.g. test fakes) degrade to None.
            get_node = getattr(self._source, "get_node", None)
            queue: deque[tuple[str, int, GraphNode | None]] = deque()
            for seed in sorted(set(seeds)):  # determinism
                seed_node: GraphNode | None = None
                if get_node is not None:
                    try:
                        fetched = await get_node(seed)
                    except Exception as exc:
                        emit_phase4_event(
                            event="graph_seed_hydrate_failed",
                            operation="graph_search",
                            status="degraded",
                            latency_ms=(time.perf_counter() - start) * 1000,
                            query_id=query_id,
                            repo_id=repo_id,
                            level="warning",
                            error=str(exc),
                            seed=seed,
                        )
                    else:
                        # Guard against non-GraphNode returns (mock
                        # sources auto-create `get_node`); anything
                        # else degrades to the legacy null seed.
                        if isinstance(fetched, GraphNode):
                            seed_node = fetched
                queue.append((seed, 0, seed_node))

            # Visit-on-pop (not on-discover): a node's neighbors are
            # reached via their queue entry, never skipped because they
            # were prematurely marked visited at enqueue time.
            while queue:
                node_id, depth, source_node = queue.popleft()
                if node_id in visited or depth > self._max_depth:
                    continue
                visited[node_id] = GraphHit(
                    node_id=node_id,
                    depth=depth,
                    proximity=self._proximity(depth),
                    file_path=source_node.file_path if source_node else None,
                    qualified_name=source_node.qualified_name if source_node else None,
                    kind=source_node.kind.value if source_node else None,
                )
                if depth >= self._max_depth:
                    continue
                # Step out by 1 level — the storage protocol's `depth`
                # parameter handles batching internally.
                try:
                    nodes = await self._source.neighbors(
                        node_id, edge_kinds=list(edge_kinds or []), depth=1,
                    )
                except Exception as exc:
                    emit_phase4_event(
                        event="graph_neighbor_failed",
                        operation="graph_search",
                        status="degraded",
                        latency_ms=(time.perf_counter() - start) * 1000,
                        query_id=query_id,
                        repo_id=repo_id,
                        level="warning",
                        error=str(exc),
                        seed=node_id,
                    )
                    continue
                for n in sorted(nodes, key=lambda x: x.node_id):
                    if n.kind == NodeKind.EXTERNAL:
                        continue
                    if n.node_id not in visited:
                        queue.append((n.node_id, depth + 1, n))

            # Sort hits deterministically before turning into candidates.
            ordered = sorted(visited.values(), key=lambda h: (h.depth, h.node_id))
            candidates = [
                RetrievalCandidate(
                    unit_id=h.node_id,
                    channel=RetrievalChannel.GRAPH,
                    raw_score=h.proximity,
                    file_path=h.file_path,
                    qualified_name=h.qualified_name,
                    kind=h.kind,
                    extra={"depth": h.depth},
                )
                for h in ordered
            ]

            elapsed = (time.perf_counter() - start) * 1000
            span.set_attribute("hits", len(candidates))
            emit_phase4_event(
                event="graph_search_done",
                operation="graph_search",
                status="success",
                latency_ms=elapsed,
                query_id=query_id,
                repo_id=repo_id,
                level="debug",
                hits=len(candidates),
            )
            return candidates

    def _proximity(self, depth: int) -> float:
        """Documented contract (schemas/retrieval.py): 1/(1+depth).

        Delegates to the canonical ranking-side scorer so retrieval and
        ranking can never drift apart. Independent of max_depth — the
        traversal bound limits WHICH nodes are visited, not their score
        (the old taper zeroed the entire requested-depth band).
        """
        # Local import: keeps core.retrieval free of an unconditional
        # import-time dependency on core.ranking (which itself imports
        # core.retrieval.logevent).
        from core.ranking.scoring import graph_proximity_from_depth

        return graph_proximity_from_depth(depth)


__all__ = ["GraphHit", "GraphRetriever", "GraphTraversalSource"]
