"""SDK result types — thin Pydantic mirrors of the API response shapes.

Each model uses `extra="ignore"` so a future server-side field
addition doesn't break older clients (forward compatibility).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _SdkBase(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class IngestResult(_SdkBase):
    repo_id: str
    commit_sha: str
    units_collection: str
    metrics: dict[str, float | int]
    failed_files: list[str]


class ReembedResult(_SdkBase):
    repo_id: str
    units_total: int
    units_embedded: int
    # Failed BATCHES (not units) — mirrors the API's `failed_batches`.
    failed_batches: int


class RetrieveResult(_SdkBase):
    query_id: str
    repo_id: str
    packet: dict[str, Any]
    graph_hits: int
    vector_hits: int
    metadata_hits: int
    final_candidates: int
    ranked_count: int
    failed_channels: list[str]
    latency_ms: float


class QueryGraphResult(_SdkBase):
    """Result returned by the MCP `query_graph` tool, unwrapped for SDK use."""

    node: str
    found: bool
    depth: int = 1
    candidates: list[dict[str, Any]] = []
    # Real directed edges among the returned candidates (additive; older
    # servers simply omit it).
    edges: list[dict[str, Any]] = []


class McpToolResult(_SdkBase):
    tool: str
    request_id: str
    status: str
    data: dict[str, Any] = {}
    error: str | None = None
    error_code: str | None = None
    latency_ms: float = 0.0


class SnapshotResult(_SdkBase):
    snapshot_id: str
    tenant_id: str
    captured_at: str
    components: dict[str, str]


class ReplayResult(_SdkBase):
    snapshot_id: str
    matches: bool
    expected_hash: str
    actual_hash: str
    notes: str = ""


class StatusResult(_SdkBase):
    service: str
    environment: str
    safe_mode: dict[str, Any]
    feature_flags: list[dict[str, Any]]
    boot_overall_ok: bool
    boot_failed_stages: list[str]
    boot_degraded_stages: list[str]
    boot_stages: list[dict[str, Any]]
    mcp_tool_count: int
    schema_version: str
    # Phase-9.5 additions (older servers omit them — defaults keep us lenient).
    embeddings_enabled: bool = False
    feature_weights: dict[str, float] = {}


# ---------------------------------------------------------------------------
# v2 agent-first tool results (thin mirrors of /mcp/tools/* payloads)
# ---------------------------------------------------------------------------
class RepoSummary(_SdkBase):
    repo_id: str
    units: int
    files: int
    languages: list[str] = []


class ReposResult(_SdkBase):
    """GET /repos — every ingested repo with aggregate counts."""

    schema_version: str = ""
    repos: list[RepoSummary] = []


class SearchHit(_SdkBase):
    repo_id: str | None = None
    qualified_name: str | None = None
    kind: str | None = None
    file_path: str | None = None
    lines: str | None = None
    score: float = 0.0
    channels: list[str] = []
    snippet: str = ""
    snippet_truncated: bool = False


class SearchCodeResult(_SdkBase):
    results: list[SearchHit] = []
    total_matches: int = 0
    truncated: bool = False
    hint: str | None = None
    error: str | None = None
    failed_repos: list[str] = []
    valid_repo_ids: list[str] = []


class ReadUnitResult(_SdkBase):
    found: bool = False
    unit_id: str | None = None
    repo_id: str | None = None
    qualified_name: str | None = None
    kind: str | None = None
    file_path: str | None = None
    lines: str | None = None
    language: str | None = None
    signature: str | None = None
    docstring: str | None = None
    imports: list[str] = []
    calls: list[str] = []
    bases: list[str] = []
    content: str = ""
    truncated: bool = False
    parent_chain: list[dict[str, str]] = []
    # Miss path:
    reference: str | None = None
    suggestions: list[dict[str, str]] = []
    hint: str | None = None
    error: str | None = None
    valid_repo_ids: list[str] = []


class ExploreNeighbor(_SdkBase):
    node_id: str | None = None
    qualified_name: str | None = None
    kind: str | None = None
    file_path: str | None = None
    lines: str | None = None
    signature: str | None = None
    snippet: str | None = None
    distance: int = 1
    relation: str = ""


class ExploreResult(_SdkBase):
    found: bool = False
    seed: dict[str, Any] | None = None
    direction: str = "all"
    depth: int = 1
    neighbors: list[ExploreNeighbor] = []
    edges: list[dict[str, Any]] = []
    truncated: bool = False
    truncated_edges: bool = False
    warning: str | None = None
    # Miss path:
    qualified_name: str | None = None
    suggestions: list[dict[str, str]] = []
    hint: str | None = None
    error: str | None = None
    valid_repo_ids: list[str] = []


class SymbolMatch(_SdkBase):
    repo_id: str | None = None
    qualified_name: str
    kind: str | None = None
    file_path: str | None = None
    lines: str | None = None
    unit_id: str | None = None


class FindSymbolResult(_SdkBase):
    matches: list[SymbolMatch] = []
    truncated: bool = False
    hint: str | None = None
    error: str | None = None
    valid_repo_ids: list[str] = []


class RepoOverviewResult(_SdkBase):
    found: bool = False
    repo_id: str | None = None
    units: int = 0
    files: int = 0
    languages: dict[str, int] = {}
    unit_kinds: dict[str, int] = {}
    module_tree: list[dict[str, Any]] = []
    largest_modules: list[dict[str, Any]] = []
    most_connected: list[dict[str, Any]] = []
    doc_files: list[str] = []
    note: str | None = None
    hint: str | None = None
    error: str | None = None
    valid_repo_ids: list[str] = []
