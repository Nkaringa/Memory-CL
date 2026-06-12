"""Repo discovery surface — aggregate listing of every ingested repo.

Unauthenticated read-only endpoint (same posture as ``/status``); it
feeds the UI's repo selectors so first-time users never have to guess
a ``repo_id``.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from apps.api.dependencies import AppStateDep

router = APIRouter(prefix="/repos", tags=["repos"])


class RepoView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    units: int
    files: int
    languages: list[str]


class ReposResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str
    repos: list[RepoView]


class QnameMatchView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qualified_name: str
    kind: str


class QnamesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    matches: list[QnameMatchView]


class GraphNodeView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: str
    kind: str
    qualified_name: str
    name: str
    file_path: str | None
    line_start: int | None
    line_end: int | None


class GraphEdgeView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    src_id: str
    kind: str
    dst_id: str


class GraphCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: int
    edges: int


class RepoGraphResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    truncated: bool
    nodes: list[GraphNodeView]
    edges: list[GraphEdgeView]
    counts: GraphCounts


_MAX_GRAPH_NODES = 20_000


@router.get("", response_model=ReposResponse)
async def list_repos(state: AppStateDep) -> ReposResponse:
    """One aggregate row per ingested repo: unit/file counts + languages."""
    summaries = await state.units_repo.list_repos()

    from schemas.base import SCHEMA_VERSION
    return ReposResponse(
        schema_version=SCHEMA_VERSION,
        repos=[
            RepoView(
                repo_id=s.repo_id,
                units=s.units,
                files=s.files,
                languages=sorted(s.languages),
            )
            for s in summaries
        ],
    )


@router.get("/{repo_id}/qnames", response_model=QnamesResponse)
async def search_qnames(
    repo_id: str,
    state: AppStateDep,
    q: str = Query(min_length=1, description="substring to match (case-insensitive)"),
    limit: int = Query(default=20, gt=0),
) -> QnamesResponse:
    """Qualified-name autocomplete: substring matches, shortest first."""
    matches = await state.units_repo.search_qnames(repo_id, q, limit=min(limit, 100))
    return QnamesResponse(
        repo_id=repo_id,
        matches=[
            QnameMatchView(qualified_name=m.qualified_name, kind=m.kind)
            for m in matches
        ],
    )


@router.get("/{repo_id}/graph", response_model=RepoGraphResponse)
async def repo_graph(
    repo_id: str,
    state: AppStateDep,
    include_external: bool = Query(
        default=False, description="include unresolved External nodes"
    ),
    max_nodes: int = Query(default=5000, gt=0),
) -> RepoGraphResponse:
    """Whole-repo graph snapshot: every node plus all edges among them.

    `truncated` is true when the node count hit `max_nodes` — the graph
    may be incomplete and the caller should raise the cap (≤ 20000).

    Note: `truncated` is also true when the repo contains exactly
    `max_nodes` nodes (a false positive: the graph is complete but the
    count equals the cap, so we cannot tell without a second query).
    """
    max_nodes = min(max_nodes, _MAX_GRAPH_NODES)
    nodes, edges = await state.graph_repo.repo_graph(
        repo_id, include_external=include_external, max_nodes=max_nodes
    )
    return RepoGraphResponse(
        repo_id=repo_id,
        truncated=len(nodes) >= max_nodes,
        nodes=[
            GraphNodeView(
                node_id=n.node_id,
                kind=n.kind.value,
                qualified_name=n.qualified_name,
                name=n.name,
                file_path=n.file_path,
                line_start=n.line_start,
                line_end=n.line_end,
            )
            for n in nodes
        ],
        edges=[
            GraphEdgeView(src_id=src, kind=kind, dst_id=dst)
            for src, kind, dst in edges
        ],
        counts=GraphCounts(nodes=len(nodes), edges=len(edges)),
    )
