"""Graph compactor — compute (do not apply) merge plans for the graph.

Per spec we MUST:
    * merge low-importance nodes into module-level summaries
    * preserve dependency edges
    * keep structural integrity
    * never break Phase 2 graph invariants

The compactor produces a `GraphCompactionPlan` describing which nodes
would be folded into which module aggregates. Applying the plan is
out of Phase-6 scope — Phase 2 graph schemas are immutable.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from core.lifecycle.logevent import emit_phase6_event
from core.lifecycle.relevance_scorer import RelevanceBreakdown
from core.observability import get_tracer
from schemas import GraphEdge, GraphNode, NodeKind

_tracer = get_tracer("core.lifecycle.graph_compactor")


@dataclass(frozen=True, slots=True)
class GraphMerge:
    """Single merge instruction: fold `merged_node_ids` into `target`."""

    target_node_id: str
    target_kind: NodeKind
    merged_node_ids: tuple[str, ...]
    preserved_edge_ids: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class GraphCompactionPlan:
    merges: tuple[GraphMerge, ...]

    @property
    def merged_count(self) -> int:
        return sum(len(m.merged_node_ids) for m in self.merges)


class GraphCompactor:
    """Plan node-level compaction without mutating Phase-2 storage.

    A node is a compaction candidate iff:
        * it is a leaf kind (Function / Method / Constant)
        * its centrality (in_degree → score) is below threshold
        * it is NOT already EXTERNAL (those are noise we keep)
    Each candidate folds into its module-level aggregate. Edges
    pointing into / out of the candidate are PRESERVED but rewritten
    in the plan to terminate at the module aggregate (`preserved_edge_ids`
    captures the rewrite). Plan application is downstream.
    """

    _COMPACTABLE: ClassVar[frozenset[NodeKind]] = frozenset(
        {NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CONSTANT}
    )

    def __init__(self, *, centrality_threshold: float) -> None:
        if not 0.0 <= centrality_threshold <= 1.0:
            raise ValueError("centrality_threshold must be in [0, 1]")
        self._threshold = centrality_threshold

    def plan(
        self,
        *,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        scores: dict[str, RelevanceBreakdown],
    ) -> GraphCompactionPlan:
        start = time.perf_counter()
        with _tracer.start_as_current_span("graph_compactor.merge") as span:
            span.set_attribute("node_count", len(nodes))
            span.set_attribute("edge_count", len(edges))

            nodes_by_id = {n.node_id: n for n in nodes}
            module_for_qname = {
                n.qualified_name: n.node_id
                for n in nodes
                if n.kind == NodeKind.MODULE
            }

            # Group candidates by their owning module's node_id.
            candidates_by_module: dict[str, list[GraphNode]] = defaultdict(list)
            for n in nodes:
                if n.kind not in self._COMPACTABLE:
                    continue
                breakdown = scores.get(n.node_id)
                if breakdown is None or breakdown.centrality >= self._threshold:
                    continue
                module_id = self._module_for(n, module_for_qname)
                if module_id is None:
                    continue  # orphan — never merge orphans (decay handles them)
                candidates_by_module[module_id].append(n)

            merges: list[GraphMerge] = []
            for module_id in sorted(candidates_by_module):
                module_node = nodes_by_id[module_id]
                victims = sorted(
                    candidates_by_module[module_id], key=lambda n: n.node_id
                )
                victim_ids = {v.node_id for v in victims}

                preserved = self._preserved_edges(edges, victim_ids, module_id)
                merges.append(
                    GraphMerge(
                        target_node_id=module_node.node_id,
                        target_kind=NodeKind.MODULE,
                        merged_node_ids=tuple(v.node_id for v in victims),
                        preserved_edge_ids=preserved,
                    )
                )

            elapsed = (time.perf_counter() - start) * 1000
            emit_phase6_event(
                event="memory_evolution",
                entity_id="<batch>",
                operation="compact",
                relevance_score=0.0,
                status="success",
                level="info",
                latency_ms=round(elapsed, 3),
                merges=len(merges),
                merged_nodes=sum(len(m.merged_node_ids) for m in merges),
            )
            return GraphCompactionPlan(merges=tuple(merges))

    @staticmethod
    def _module_for(
        node: GraphNode, module_for_qname: dict[str, str],
    ) -> str | None:
        """Find the module node_id that owns a child by qname prefix."""
        # Function qnames are `pkg.mod.fn`; trim segments until we hit a known module.
        parts = node.qualified_name.split(".")
        for i in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:i])
            if candidate in module_for_qname:
                return module_for_qname[candidate]
        return None

    @staticmethod
    def _preserved_edges(
        edges: Sequence[GraphEdge],
        victim_ids: set[str],
        module_id: str,
    ) -> tuple[tuple[str, str, str], ...]:
        """Rewrite each edge touching a victim to terminate at the module."""
        rewritten: set[tuple[str, str, str]] = set()
        for e in edges:
            src_in = e.src_id in victim_ids
            dst_in = e.dst_id in victim_ids
            if not src_in and not dst_in:
                continue
            new_src = module_id if src_in else e.src_id
            new_dst = module_id if dst_in else e.dst_id
            if new_src == new_dst:
                continue  # collapsed self-edge — drop
            rewritten.add((new_src, e.kind.value, new_dst))
        return tuple(sorted(rewritten))


__all__ = ["GraphCompactionPlan", "GraphCompactor", "GraphMerge"]
