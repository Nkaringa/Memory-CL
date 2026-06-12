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
