"""Freshness surface — inspect + control auto-reingest (Phase 3).

Read (`GET /freshness`) is unauthenticated like `/repos`/`/status`; every
mutation (add managed repo, pause/resume, force-sync, remove) requires the
API key. Managed-add and force-sync build a `SubprocessGitRunner` + bind
`run_ingest` on the spot, so they work even with the background loops
disabled.
"""

from __future__ import annotations

import shutil
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.dependencies import AppStateDep, RepoRegistryDep, RuntimeConfigDep
from apps.api.freshness.git import SubprocessGitRunner
from apps.api.freshness.managed import add_managed_repo, sync_managed_repo
from apps.api.routers.ingest import RepoPathError, run_ingest
from apps.api.state import AppState
from apps.mcp.auth import ApiKeyDep
from core import get_settings
from core.config_runtime import RuntimeConfig
from storage.repo_registry_repo import RepoRegistryRow

router = APIRouter(prefix="/freshness", tags=["freshness"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class FreshnessRepoView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    source_type: str
    repo_path: str
    remote_url: str | None
    branch: str | None
    last_commit_sha: str | None
    watch_enabled: bool
    last_synced_at: datetime | None
    last_change_at: datetime | None
    last_error: str | None


class FreshnessListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    freshness_enabled: bool
    repos: list[FreshnessRepoView]


class AddManagedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    remote_url: str = Field(min_length=1, max_length=512)
    branch: str | None = Field(default=None, max_length=255)
    repo_id: str | None = Field(default=None, min_length=1, max_length=128)


class AddManagedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    commit_sha: str | None


class ToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class SyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    changed: bool
    new_sha: str | None = None
    error: str | None = None


class OkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool = True


def _view(r: RepoRegistryRow) -> FreshnessRepoView:
    return FreshnessRepoView(
        repo_id=r.repo_id, source_type=r.source_type, repo_path=r.repo_path,
        remote_url=r.remote_url, branch=r.branch, last_commit_sha=r.last_commit_sha,
        watch_enabled=r.watch_enabled, last_synced_at=r.last_synced_at,
        last_change_at=r.last_change_at, last_error=r.last_error,
    )


def _make_ingest(state: AppState, runtime: RuntimeConfig):  # type: ignore[no-untyped-def]
    settings = get_settings()

    async def _ingest(*, repo_id: str, repo_path: str, commit_sha: str) -> object:
        return await run_ingest(
            state, settings, runtime,
            repo_id=repo_id, repo_path=repo_path, commit_sha=commit_sha,
        )

    return _ingest


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=FreshnessListResponse)
async def list_freshness(registry: RepoRegistryDep) -> FreshnessListResponse:
    """Every registered repo + its freshness state. Unauthenticated read."""
    rows = await registry.list_all()
    return FreshnessListResponse(
        freshness_enabled=get_settings().freshness_enabled,
        repos=[_view(r) for r in rows],
    )


@router.post("/managed", response_model=AddManagedResponse)
async def add_managed(
    body: AddManagedRequest,
    state: AppStateDep,
    runtime: RuntimeConfigDep,
    registry: RepoRegistryDep,
    api_key: ApiKeyDep,
) -> AddManagedResponse:
    """Clone a git URL into the managed workspace, register it, and ingest.

    The clone is kept fresh by the poller. Private repos need
    `GITHUB_TOKEN` configured (injected into the clone URL; the clean URL
    is what's stored)."""
    settings = get_settings()
    token = (
        settings.github_token.get_secret_value() if settings.github_token else None
    )
    try:
        result = await add_managed_repo(
            registry=registry,
            git=SubprocessGitRunner(),
            ingest=_make_ingest(state, runtime),
            remote_url=body.remote_url,
            branch=body.branch,
            repo_id=body.repo_id,
            managed_root=settings.managed_repos_root,
            github_token=token,
        )
    except (ValueError, RepoPathError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:  # git clone / network failures
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"could not add managed repo: {exc}",
        ) from exc
    return AddManagedResponse(repo_id=result.repo_id, commit_sha=result.new_sha)


@router.post("/{repo_id}/toggle", response_model=OkResponse)
async def toggle_watch(
    repo_id: str,
    body: ToggleRequest,
    registry: RepoRegistryDep,
    api_key: ApiKeyDep,
) -> OkResponse:
    """Pause/resume freshness for one repo (watcher + poller both honor it)."""
    if await registry.get(repo_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown repo_id")
    await registry.set_watch_enabled(repo_id, body.enabled)
    return OkResponse()


@router.post("/{repo_id}/sync", response_model=SyncResponse)
async def sync_now(
    repo_id: str,
    state: AppStateDep,
    runtime: RuntimeConfigDep,
    registry: RepoRegistryDep,
    api_key: ApiKeyDep,
) -> SyncResponse:
    """Force a freshness check now: managed repos fetch + reingest if the
    branch moved; local repos reingest their current on-disk state."""
    repo = await registry.get(repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown repo_id")
    ingest = _make_ingest(state, runtime)
    if repo.source_type == "managed":
        res = await sync_managed_repo(
            repo, registry=registry, git=SubprocessGitRunner(), ingest=ingest
        )
        return SyncResponse(
            repo_id=res.repo_id, changed=res.changed, new_sha=res.new_sha, error=res.error
        )
    # Local: reingest the current working tree.
    try:
        await ingest(
            repo_id=repo.repo_id,
            repo_path=repo.repo_path,
            commit_sha=repo.last_commit_sha or "auto",
        )
    except RepoPathError as exc:
        await registry.mark_error(repo_id, str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    await registry.mark_synced(repo_id, repo.last_commit_sha)
    return SyncResponse(repo_id=repo_id, changed=True, new_sha=repo.last_commit_sha)


@router.delete("/{repo_id}", response_model=OkResponse)
async def remove_repo(
    repo_id: str,
    request: Request,
    registry: RepoRegistryDep,
    api_key: ApiKeyDep,
) -> OkResponse:
    """Deregister a repo from freshness. For a managed repo the clone is
    deleted from the workspace too (the ingested memory rows remain)."""
    repo = await registry.get(repo_id)
    if repo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown repo_id")
    settings = get_settings()
    if repo.source_type == "managed" and repo.repo_path.startswith(
        settings.managed_repos_root
    ):
        # Best-effort clone cleanup — never fail the deregister on an fs error.
        shutil.rmtree(repo.repo_path, ignore_errors=True)
    await registry.delete(repo_id)
    return OkResponse()


__all__ = ["router"]
