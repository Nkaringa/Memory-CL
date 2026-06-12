from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from schemas import GraphEdge, GraphNode, IngestionUnit


# ---------------------------------------------------------------------------
# Postgres — durable canonical store for IngestionUnits
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RepoSummary:
    """Aggregate view of one ingested repo (for discovery surfaces)."""

    repo_id: str
    units: int
    files: int
    languages: tuple[str, ...]


@runtime_checkable
class IngestionUnitRepository(Protocol):
    """Phase 2 write path for the canonical unit store (Postgres).

    Idempotency contract:
        upsert_unit(u) is a no-op if a row with `unit_id = u.unit_id` and
        identical `source_sha` already exists. If `source_sha` differs the
        row is replaced and `updated_at` is bumped.
    """

    async def upsert_unit(self, unit: IngestionUnit) -> bool:
        """Returns True iff the row was inserted or content-changed."""
        ...

    async def upsert_units(self, units: Iterable[IngestionUnit]) -> int:
        """Bulk variant — returns the number of rows actually changed."""
        ...

    async def get_unit(self, unit_id: str) -> IngestionUnit | None: ...

    async def list_units_for_file(
        self, repo_id: str, file_path: str
    ) -> Sequence[IngestionUnit]: ...

    async def list_units_for_repo(self, repo_id: str) -> Sequence[IngestionUnit]:
        """Every unit in the repo — drives the reembed backfill surface."""
        ...

    async def delete_units_for_file(self, repo_id: str, file_path: str) -> int:
        """Used during file-level reconciliation (rename/remove)."""
        ...

    async def list_repos(self) -> Sequence[RepoSummary]:
        """Aggregate per-repo counts for the discovery surface (GET /repos)."""
        ...


# ---------------------------------------------------------------------------
# Neo4j — graph relations only
# ---------------------------------------------------------------------------
@runtime_checkable
class GraphRepository(Protocol):
    """Phase 2 write path for structural relations (Neo4j).

    Idempotency contract:
        - Nodes are MERGEd by `node_id`. Properties are overwritten on each
          ingest from the same commit; a fresh commit_sha rotates provenance.
        - Edges are MERGEd by (src_id, kind, dst_id). `commit_sha` is updated
          but the edge itself is never duplicated.

    Validation contract:
        upsert_edge MUST reject any edge violating `schemas.graph.EDGE_RULES`.
    """

    async def upsert_node(self, node: GraphNode) -> None: ...

    async def upsert_nodes(self, nodes: Iterable[GraphNode]) -> int: ...

    async def upsert_edge(self, edge: GraphEdge) -> None: ...

    async def upsert_edges(self, edges: Iterable[GraphEdge]) -> int: ...

    async def neighbors(
        self,
        node_id: str,
        edge_kinds: Sequence[str] | None = None,
        depth: int = 1,
    ) -> Sequence[GraphNode]: ...

    async def delete_subgraph_for_file(self, repo_id: str, file_path: str) -> int:
        """Detach-delete every node whose file_path matches; for reconciliation."""
        ...


# ---------------------------------------------------------------------------
# Qdrant — vector + payload store
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class VectorPoint:
    """Pre-embedding payload-only descriptor written by Phase 2.

    Phase 2 writes the payload (and optionally a zero-vector placeholder
    if the configured Qdrant collection requires it). Phase 3 fills the
    real `vector` via the embedding pipeline. This split keeps parsing
    deterministic and decoupled from any LLM-side dependency.
    """

    point_id: str            # MUST equal IngestionUnit.unit_id
    repo_id: str
    qualified_name: str
    kind: str                # UnitKind value
    file_path: str
    line_start: int
    line_end: int
    commit_sha: str
    source_sha: str
    vector: tuple[float, ...] | None = None  # filled in Phase 3


@dataclass(frozen=True, slots=True)
class VectorHit:
    point_id: str
    score: float
    payload: dict[str, object]


@runtime_checkable
class VectorRepository(Protocol):
    """Phase 2 write path for the vector store (Qdrant).

    Phase 2 only upserts payloads. Vector search is a Phase 4 concern.
    Idempotency contract:
        upsert_payload(p) on the same point_id replaces the payload but
        leaves any existing vector intact.
    """

    async def ensure_collection(self, name: str, vector_size: int) -> None: ...

    async def upsert_payload(self, collection: str, point: VectorPoint) -> None: ...

    async def upsert_payloads(
        self, collection: str, points: Iterable[VectorPoint]
    ) -> int: ...

    async def delete_points_for_file(
        self, collection: str, repo_id: str, file_path: str
    ) -> int: ...
