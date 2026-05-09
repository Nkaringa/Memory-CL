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
