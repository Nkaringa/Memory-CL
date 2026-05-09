"""Request schemas for the seven mandated MCP tools.

These models are MCP-INTERNAL contracts: they define the public
agent-facing tool surface. They do NOT replace or override any
Phase 1-4 schema; they wrap and project them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _BaseToolRequest(BaseModel):
    """Common config for every tool request."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class GetContextRequest(_BaseToolRequest):
    task: str = Field(min_length=1, max_length=8192)
    scope: str | None = Field(default=None, description="Optional repo_id alias")
    repo_id: str = Field(min_length=1, max_length=128)
    top_k: int = Field(default=10, gt=0, le=200)
    seed_unit_ids: list[str] = Field(default_factory=list)


class GetModuleSummaryRequest(_BaseToolRequest):
    module: str = Field(min_length=1, max_length=512, description="Module qname")
    repo_id: str = Field(min_length=1, max_length=128)


class GetRelatedComponentsRequest(_BaseToolRequest):
    component: str = Field(min_length=1, max_length=512,
                           description="Either a unit_id or qualified_name")
    repo_id: str = Field(min_length=1, max_length=128)
    depth: int = Field(default=1, gt=0, le=10)


class GetRisksRequest(_BaseToolRequest):
    entity: str = Field(min_length=1, max_length=512)
    repo_id: str = Field(min_length=1, max_length=128)


class UpdateMemoryRequest(_BaseToolRequest):
    session_id: str = Field(min_length=1, max_length=128)
    repo_id: str = Field(min_length=1, max_length=128)
    session_data: dict[str, Any] = Field(
        description="Append-only payload — the MCP layer treats this opaquely",
    )


class IngestRepositoryRequest(_BaseToolRequest):
    path: str = Field(min_length=1)
    repo_id: str = Field(min_length=1, max_length=128)
    commit_sha: str = Field(default="manual", min_length=1, max_length=64)


class QueryGraphRequest(_BaseToolRequest):
    node: str = Field(min_length=1, description="unit_id or qualified_name")
    repo_id: str = Field(min_length=1, max_length=128)
    depth: int = Field(default=1, gt=0, le=10)
