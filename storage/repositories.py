from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from schemas import GraphEdge, GraphNode, IngestionUnit

if TYPE_CHECKING:
    from storage.auth_provider_repo import AuthProviderRow
    from storage.membership_repo import MembershipRow
    from storage.org_repo import OrgRow
    from storage.session_repo import SessionRow
    from storage.user_repo import UserRow


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


@dataclass(frozen=True, slots=True)
class QnameMatch:
    """One qualified-name autocomplete hit (for the discovery surface)."""

    qualified_name: str
    kind: str


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

    async def search_qnames(
        self, repo_id: str, query: str, limit: int = 20
    ) -> Sequence[QnameMatch]:
        """Substring search over qualified names (autocomplete surface).

        Matching is case-insensitive; LIKE metacharacters in `query` are
        treated literally. Shorter qualified names sort first so canonical
        units beat deeply nested test paths.
        """
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

    async def edges_among(self, node_ids: Sequence[str]) -> list[tuple[str, str, str]]: ...

    async def repo_graph(
        self,
        repo_id: str,
        *,
        include_external: bool = False,
        max_nodes: int = 5000,
    ) -> tuple[list[GraphNode], list[tuple[str, str, str]]]:
        """Whole-repo snapshot: nodes (sorted, capped at `max_nodes` after
        clamping to [1, 20000]) plus all directed edges among them."""
        ...

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

    async def recreate_collection(self, name: str, vector_size: int) -> None:
        """Drop the collection (if present) and recreate it empty at
        `vector_size`. Used when the embedding dimension changes (mode
        switch) — `ensure_collection` can't resize an existing collection,
        so the old-dimension vectors must be dropped before re-embedding."""
        ...

    async def upsert_payload(self, collection: str, point: VectorPoint) -> None: ...

    async def upsert_payloads(
        self, collection: str, points: Iterable[VectorPoint]
    ) -> int: ...

    async def delete_points_for_file(
        self, collection: str, repo_id: str, file_path: str
    ) -> int: ...


# ---------------------------------------------------------------------------
# Organizations — durable tenant boundary
# ---------------------------------------------------------------------------
@runtime_checkable
class OrgRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def create_org(self, *, org_id: str, name: str, slug: str) -> "OrgRow": ...
    async def get_org(self, org_id: str) -> "OrgRow | None": ...
    async def get_org_by_slug(self, slug: str) -> "OrgRow | None": ...
    async def list_orgs(self) -> "list[OrgRow]": ...
    async def ensure_default_org(self) -> "OrgRow": ...


# ---------------------------------------------------------------------------
# Users + local credentials — human identity
# ---------------------------------------------------------------------------
@runtime_checkable
class UserRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def create_user(self, *, user_id: str, email: str, display_name: str, avatar_url: str = "") -> "UserRow": ...
    async def get_user(self, user_id: str) -> "UserRow | None": ...
    async def get_by_email(self, email: str) -> "UserRow | None": ...
    async def count_users(self) -> int: ...
    async def set_password(self, *, user_id: str, password_hash: str) -> None: ...
    async def get_password_hash(self, user_id: str) -> "str | None": ...


# ---------------------------------------------------------------------------
# Memberships — user ↔ org association with role
# ---------------------------------------------------------------------------
@runtime_checkable
class MembershipRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def add_member(self, *, membership_id: str, user_id: str, org_id: str, role: str, status: str = "active") -> "MembershipRow": ...
    async def get_membership(self, *, user_id: str, org_id: str) -> "MembershipRow | None": ...
    async def list_orgs_for_user(self, user_id: str) -> "list[MembershipRow]": ...
    async def list_members(self, *, org_id: str) -> "list[MembershipRow]": ...
    async def set_role(self, *, user_id: str, org_id: str, role: str) -> None: ...


# ---------------------------------------------------------------------------
# Sessions — server-side session store (cookie-hash keyed)
# ---------------------------------------------------------------------------
@runtime_checkable
class SessionRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def create_session(self, *, session_id: str, user_id: str, active_org_id: str, csrf_token: str, expires_at: datetime) -> "SessionRow": ...
    async def get_active(self, session_id: str) -> "SessionRow | None": ...
    async def revoke(self, session_id: str) -> None: ...
    async def list_active_session_ids(self) -> "set[str]": ...


# ---------------------------------------------------------------------------
# Auth providers — OIDC/OAuth2 provider configuration
# ---------------------------------------------------------------------------
@runtime_checkable
class AuthProviderRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def create(self, *, id: str, provider_type: str, display_name: str, client_id: str, client_secret: str, discovery_url: str | None, scopes: str | None, enabled: bool) -> "AuthProviderRow": ...
    async def get(self, id: str) -> "AuthProviderRow | None": ...
    async def list_all(self) -> "list[AuthProviderRow]": ...
    async def list_enabled(self) -> "list[AuthProviderRow]": ...
    async def update(self, *, id: str, display_name: str, client_id: str, client_secret: str, discovery_url: str | None, scopes: str | None) -> "AuthProviderRow": ...
    async def set_enabled(self, *, id: str, enabled: bool) -> None: ...
    async def delete(self, id: str) -> None: ...
