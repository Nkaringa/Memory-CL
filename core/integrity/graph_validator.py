"""Graph integrity validator.

Checks the Phase-2/4 invariants without modifying anything:
    * no orphan edges (every src_id and dst_id resolves to a node)
    * no self-edges (Phase-2 schema bans them but storage corruption
      could re-introduce them)
    * no edges that violate `EDGE_RULES`
    * `unit_id == node_id` for every non-EXTERNAL node
    * EDGE_RULES enforcement still holds
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from schemas import GraphEdge, GraphNode, NodeKind, is_edge_allowed


@dataclass(frozen=True, slots=True)
class IntegrityViolation:
    kind: str       # "orphan_edge" | "self_edge" | "edge_rule" | "id_mismatch"
    detail: str
    src_id: str = ""
    dst_id: str = ""
    edge_kind: str = ""
    node_id: str = ""


@dataclass(frozen=True, slots=True)
class GraphIntegrityReport:
    nodes_checked: int
    edges_checked: int
    violations: tuple[IntegrityViolation, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.violations

    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.violations:
            out[v.kind] = out.get(v.kind, 0) + 1
        return dict(sorted(out.items()))


class GraphValidator:
    """Pure read-side validator. Returns a structured report.

    Per spec: never crashes the system on a violation — emits a
    detailed report so the caller can decide to quarantine.
    """

    def validate(
        self,
        *,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        units_by_id: dict[str, Any] | None = None,
    ) -> GraphIntegrityReport:
        node_by_id: dict[str, GraphNode] = {n.node_id: n for n in nodes}
        violations: list[IntegrityViolation] = []

        # ---- node-side invariants ----
        for n in nodes:
            # `unit_id == node_id` only applies to non-EXTERNAL nodes.
            if n.kind == NodeKind.EXTERNAL or units_by_id is None:
                continue
            unit = units_by_id.get(n.node_id)
            if unit is None:
                continue  # caller didn't supply the unit lookup for this id
            unit_id = getattr(unit, "unit_id", None)
            if unit_id != n.node_id:
                violations.append(
                    IntegrityViolation(
                        kind="id_mismatch",
                        detail=f"node_id {n.node_id} != unit_id {unit_id}",
                        node_id=n.node_id,
                    )
                )

        # ---- edge-side invariants ----
        for e in edges:
            src = node_by_id.get(e.src_id)
            dst = node_by_id.get(e.dst_id)
            if src is None or dst is None:
                violations.append(
                    IntegrityViolation(
                        kind="orphan_edge",
                        detail=f"missing endpoint(s) for {e.src_id}-[{e.kind.value}]->{e.dst_id}",
                        src_id=e.src_id, dst_id=e.dst_id, edge_kind=e.kind.value,
                    )
                )
                continue
            if e.src_id == e.dst_id:
                violations.append(
                    IntegrityViolation(
                        kind="self_edge",
                        detail=f"self-edge {e.src_id}-[{e.kind.value}]->{e.dst_id}",
                        src_id=e.src_id, dst_id=e.dst_id, edge_kind=e.kind.value,
                    )
                )
                continue
            if not is_edge_allowed(src.kind, e.kind, dst.kind):
                violations.append(
                    IntegrityViolation(
                        kind="edge_rule",
                        detail=(
                            f"{src.kind.value}-[{e.kind.value}]->{dst.kind.value} "
                            f"forbidden"
                        ),
                        src_id=e.src_id, dst_id=e.dst_id, edge_kind=e.kind.value,
                    )
                )

        # Sort violations for deterministic report ordering.
        violations.sort(key=lambda v: (v.kind, v.src_id, v.dst_id, v.node_id))
        return GraphIntegrityReport(
            nodes_checked=len(nodes),
            edges_checked=len(edges),
            violations=tuple(violations),
        )


__all__ = ["GraphIntegrityReport", "GraphValidator", "IntegrityViolation"]
