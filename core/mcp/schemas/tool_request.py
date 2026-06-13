"""Request schemas for the agent-facing MCP tool surface (v2).

These models are MCP-INTERNAL contracts: they define the public
agent-facing tool surface. They do NOT replace or override any
Phase 1-4 schema; they wrap and project them.

Every field carries a `description` — these are rendered verbatim into
the JSON Schema each MCP client shows the agent, so they are part of
the tool's UX, not documentation garnish.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_REPO_ID_DESC = (
    "Repository id as returned by list_repos (e.g. 'memory-cl'). "
    "Call list_repos first if you don't know it."
)
_REPO_ID_OPTIONAL_DESC = (
    "Repository id as returned by list_repos. Omit to search EVERY "
    "ingested repo (results are attributed per repo)."
)


class _BaseToolRequest(BaseModel):
    """Common config for every tool request."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# v2 agent-facing tools
# ---------------------------------------------------------------------------
class SearchCodeRequest(_BaseToolRequest):
    question: str = Field(
        min_length=1,
        max_length=8192,
        description=(
            "Natural-language question or task description, e.g. "
            "'where is the JA4 fingerprint parsed?'. Plain prose works "
            "best; for exact symbol names prefer find_symbol."
        ),
    )
    repo_id: str | None = Field(
        default=None, max_length=128, description=_REPO_ID_OPTIONAL_DESC
    )
    top_k: int = Field(
        default=8,
        gt=0,
        le=50,
        description="Max results to return (per repo when repo_id is omitted).",
    )


class ReadUnitRequest(_BaseToolRequest):
    reference: str = Field(
        min_length=1,
        max_length=1024,
        description=(
            "What to read: a qualified_name (e.g. 'core.mcp.tools.search'), "
            "a 64-char hex unit_id, or a repo-relative file path "
            "(e.g. 'core/mcp/tools/search_tool.py')."
        ),
    )
    repo_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Repository id. Omit to look the reference up across every "
            "ingested repo (first match in sorted repo order wins)."
        ),
    )


ExploreDirection = Literal[
    "callers", "callees", "imports", "imported_by", "inherits", "all"
]


class ExploreRequest(_BaseToolRequest):
    qualified_name: str = Field(
        min_length=1,
        max_length=1024,
        description=(
            "Symbol to explore from: a qualified_name (preferred) or a "
            "64-char hex unit_id."
        ),
    )
    repo_id: str = Field(min_length=1, max_length=128, description=_REPO_ID_DESC)
    direction: ExploreDirection = Field(
        default="all",
        description=(
            "Which relationships to follow: 'callers' (who calls this), "
            "'callees' (what this calls), 'imports' (what this imports), "
            "'imported_by' (who imports this), 'inherits' (base classes), "
            "or 'all' (every connected node, with edges annotated)."
        ),
    )
    depth: int = Field(
        default=1,
        gt=0,
        le=5,
        description="How many relationship hops to traverse (1 = direct only).",
    )


class FindSymbolRequest(_BaseToolRequest):
    query: str = Field(
        min_length=1,
        max_length=512,
        description=(
            "Case-insensitive substring of the symbol's qualified name, "
            "e.g. 'HybridRetr' or 'tools.search'."
        ),
    )
    repo_id: str | None = Field(
        default=None, max_length=128, description=_REPO_ID_OPTIONAL_DESC
    )
    limit: int = Field(
        default=20, gt=0, le=100, description="Max matches to return."
    )


class ListReposRequest(_BaseToolRequest):
    """No parameters — call with an empty object: {}."""


class RepoOverviewRequest(_BaseToolRequest):
    repo_id: str = Field(min_length=1, max_length=128, description=_REPO_ID_DESC)


class ReadFileRequest(_BaseToolRequest):
    file_path: str = Field(
        min_length=1,
        max_length=1024,
        description=(
            "Repo-relative file path exactly as stored, e.g. "
            "'core/retrieval/hybrid_retriever.py'. find_symbol and "
            "search_code results include the correct value."
        ),
    )
    repo_id: str = Field(min_length=1, max_length=128, description=_REPO_ID_DESC)


# ---------------------------------------------------------------------------
# Kept v1 tools
# ---------------------------------------------------------------------------
class GetModuleSummaryRequest(_BaseToolRequest):
    module: str = Field(
        min_length=1,
        max_length=512,
        description="Module qualified_name, e.g. 'core.retrieval.hybrid_retriever'.",
    )
    repo_id: str = Field(min_length=1, max_length=128, description=_REPO_ID_DESC)


class GetRisksRequest(_BaseToolRequest):
    entity: str = Field(
        min_length=1,
        max_length=512,
        description="qualified_name or 64-char hex unit_id to assess.",
    )
    repo_id: str = Field(min_length=1, max_length=128, description=_REPO_ID_DESC)


class UpdateMemoryRequest(_BaseToolRequest):
    session_id: str = Field(
        min_length=1,
        max_length=128,
        description="Your session identifier — entries append under this key.",
    )
    repo_id: str = Field(min_length=1, max_length=128, description=_REPO_ID_DESC)
    session_data: dict[str, Any] = Field(
        description=(
            "Arbitrary JSON object to append. Stored opaquely, append-only."
        ),
    )


class IngestRepositoryRequest(_BaseToolRequest):
    path: str = Field(
        min_length=1,
        description="Absolute filesystem path of the repo ON THE SERVER host.",
    )
    repo_id: str = Field(
        min_length=1,
        max_length=128,
        description="Id to ingest under. Re-using an existing id re-ingests it.",
    )
    commit_sha: str = Field(
        default="manual",
        min_length=1,
        max_length=64,
        description="Commit sha for provenance; defaults to 'manual'.",
    )


# ---------------------------------------------------------------------------
# Deprecated v1 aliases (schemas unchanged so existing sessions keep working)
# ---------------------------------------------------------------------------
class GetContextRequest(_BaseToolRequest):
    task: str = Field(
        min_length=1, max_length=8192, description="Natural-language question."
    )
    scope: str | None = Field(default=None, description="Optional repo_id alias")
    repo_id: str = Field(min_length=1, max_length=128, description=_REPO_ID_DESC)
    top_k: int = Field(default=10, gt=0, le=200)
    seed_unit_ids: list[str] = Field(default_factory=list)


class GetRelatedComponentsRequest(_BaseToolRequest):
    component: str = Field(
        min_length=1,
        max_length=512,
        description="Either a unit_id or qualified_name",
    )
    repo_id: str = Field(min_length=1, max_length=128, description=_REPO_ID_DESC)
    depth: int = Field(default=1, gt=0, le=10)


class QueryGraphRequest(_BaseToolRequest):
    node: str = Field(min_length=1, description="unit_id or qualified_name")
    repo_id: str = Field(min_length=1, max_length=128, description=_REPO_ID_DESC)
    depth: int = Field(default=1, gt=0, le=10)
