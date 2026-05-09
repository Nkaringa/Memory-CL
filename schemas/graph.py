from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NodeKind(StrEnum):
    """Neo4j label set. Mirrors UnitKind plus a dedicated File node.

    The File node is materially distinct from a Module unit: a Python
    package's `__init__.py` is one File but may host many Module-level
    children. Keeping File separate prevents over-collapsed graphs.
    """

    FILE = "File"
    MODULE = "Module"
    CLASS = "Class"
    FUNCTION = "Function"
    METHOD = "Method"
    CONSTANT = "Constant"
    EXTERNAL = "External"  # for unresolved import/call targets


class EdgeKind(StrEnum):
    """Edges produced by structural extraction (Phase 2).

    Semantic edges (DEPENDS_ON, RISK_OF, etc.) are out of Phase 2 scope.
    """

    CONTAINS = "CONTAINS"      # File -> Module/Class/Function   (structural)
    DEFINES = "DEFINES"        # Module/Class -> child symbol     (structural)
    IMPORTS = "IMPORTS"        # Module -> Module|External
    CALLS = "CALLS"            # Function|Method -> Function|Method|External
    INHERITS = "INHERITS"      # Class -> Class|External
    REFERENCES = "REFERENCES"  # any -> Symbol (weak link, optional)


# ---------------------------------------------------------------------------
# Edge creation rules
# ---------------------------------------------------------------------------
# Each rule is (source NodeKind, edge EdgeKind, allowed target NodeKinds).
# The rule table is the SINGLE source of truth for the GraphBuilder in the
# Phase 2 main step — graph writes that violate this table must be rejected.
# Determinism rule: edges are merged by (src_id, type, dst_id), so any
# duplicates collapse cleanly.
EDGE_RULES: tuple[tuple[NodeKind, EdgeKind, frozenset[NodeKind]], ...] = (
    (NodeKind.FILE, EdgeKind.CONTAINS, frozenset({
        NodeKind.MODULE, NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD,
        NodeKind.CONSTANT,
    })),
    (NodeKind.MODULE, EdgeKind.DEFINES, frozenset({
        NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.CONSTANT,
    })),
    (NodeKind.CLASS, EdgeKind.DEFINES, frozenset({
        NodeKind.METHOD, NodeKind.CONSTANT,
    })),
    (NodeKind.MODULE, EdgeKind.IMPORTS, frozenset({
        NodeKind.MODULE, NodeKind.EXTERNAL,
    })),
    (NodeKind.FUNCTION, EdgeKind.CALLS, frozenset({
        NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.EXTERNAL,
    })),
    (NodeKind.METHOD, EdgeKind.CALLS, frozenset({
        NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.EXTERNAL,
    })),
    (NodeKind.CLASS, EdgeKind.INHERITS, frozenset({
        NodeKind.CLASS, NodeKind.EXTERNAL,
    })),
    (NodeKind.FUNCTION, EdgeKind.REFERENCES, frozenset({
        NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD,
        NodeKind.CONSTANT, NodeKind.EXTERNAL,
    })),
    (NodeKind.METHOD, EdgeKind.REFERENCES, frozenset({
        NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD,
        NodeKind.CONSTANT, NodeKind.EXTERNAL,
    })),
)


def is_edge_allowed(src: NodeKind, kind: EdgeKind, dst: NodeKind) -> bool:
    """O(rules) check used by the GraphBuilder validator."""
    for s, k, allowed in EDGE_RULES:
        if s == src and k == kind and dst in allowed:
            return True
    return False


class GraphNode(BaseModel):
    """Minimal Neo4j node contract.

    `node_id` MUST equal `unit_id` for non-EXTERNAL nodes — this is what
    makes Postgres ↔ Neo4j ↔ Qdrant joins trivial. EXTERNAL nodes use
    "external:<qname>" so unresolved targets don't collide with real units.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    kind: NodeKind
    repo_id: str
    qualified_name: str
    name: str
    file_path: str | None = None  # None only for EXTERNAL nodes
    line_start: int | None = None
    line_end: int | None = None
    commit_sha: str | None = None  # provenance; None for EXTERNAL
    source_sha: str | None = None  # None for EXTERNAL


class GraphEdge(BaseModel):
    """Minimal Neo4j edge contract.

    The (src_id, kind, dst_id) tuple is the natural merge key. `commit_sha`
    is provenance only — re-ingesting the same commit must be a no-op.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    src_id: str
    kind: EdgeKind
    dst_id: str
    repo_id: str
    commit_sha: str
    weight: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("dst_id")
    @classmethod
    def _no_self_edges(cls, v: str, info: object) -> str:
        data = getattr(info, "data", {}) or {}
        if data.get("src_id") == v:
            raise ValueError("self-edges are not allowed")
        return v
