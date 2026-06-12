"""Repo discovery surface — aggregate listing of every ingested repo.

Unauthenticated read-only endpoint (same posture as ``/status``); it
feeds the UI's repo selectors so first-time users never have to guess
a ``repo_id``.
"""

from __future__ import annotations

from fastapi import APIRouter
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
