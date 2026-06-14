"""Wire the freshness background tasks (poller + watcher) into the app.

Kept out of `lifespan.py` so the orchestration stays readable. Both loops
run in-process as asyncio tasks, share one `RepoLocks` (so the poller and
watcher never reingest the same repo at once), and reuse the in-process
`run_ingest`. `start_freshness` returns the task handles; `stop_freshness`
cancels them cleanly on shutdown.
"""

from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI

from apps.api.freshness.git import SubprocessGitRunner
from apps.api.freshness.locks import RepoLocks
from apps.api.freshness.managed import IngestFn
from apps.api.freshness.poller import FreshnessPoller
from apps.api.freshness.watcher import FreshnessWatcher
from apps.api.routers.ingest import run_ingest
from apps.api.state import AppState
from core.config import Settings
from core.config_runtime import RuntimeConfig
from core.logging import get_logger
from storage.repo_registry_repo import RepoRegistryRepository

_log = get_logger(__name__)


def _make_ingest(
    state: AppState, settings: Settings, runtime: RuntimeConfig
) -> IngestFn:
    """Bind `run_ingest` to the live state/settings/runtime for the loops."""

    async def _ingest(*, repo_id: str, repo_path: str, commit_sha: str) -> object:
        return await run_ingest(
            state,
            settings,
            runtime,
            repo_id=repo_id,
            repo_path=repo_path,
            commit_sha=commit_sha,
        )

    return _ingest


def start_freshness(
    app: FastAPI,
    *,
    state: AppState,
    settings: Settings,
    runtime: RuntimeConfig,
    registry: RepoRegistryRepository,
) -> list[asyncio.Task[None]]:
    """Start the poller (+ watcher) as background tasks. Returns their
    handles for shutdown. No-op (empty list) when freshness is disabled."""
    if not settings.freshness_enabled:
        _log.info("freshness_disabled")
        return []

    locks = RepoLocks()
    git = SubprocessGitRunner()
    ingest = _make_ingest(state, settings, runtime)

    def safe_mode_active() -> bool:
        sm = getattr(app.state, "safe_mode", None)
        return bool(sm is not None and sm.status.enabled)

    # Shared on app.state so endpoints (force-sync, add-managed) can reuse
    # the same locks / git runner / ingest binding.
    app.state.repo_locks = locks
    app.state.freshness_git = git
    app.state.freshness_ingest = ingest

    tasks: list[asyncio.Task[None]] = []
    poller = FreshnessPoller(
        registry=registry,
        git=git,
        ingest=ingest,
        locks=locks,
        interval_seconds=settings.freshness_poll_interval_seconds,
        safe_mode_active=safe_mode_active,
    )
    app.state.freshness_poller = poller
    tasks.append(asyncio.create_task(poller.run(), name="freshness-poller"))

    if settings.freshness_watch_enabled and os.path.isdir(settings.local_repos_root):
        from watchfiles import awatch

        watcher = FreshnessWatcher(
            registry=registry,
            ingest=ingest,
            locks=locks,
            watch_root=settings.local_repos_root,
            awatch_factory=awatch,
            git=git,
            safe_mode_active=safe_mode_active,
            debounce_ms=settings.freshness_debounce_ms,
            force_polling=settings.freshness_force_polling,
        )
        tasks.append(asyncio.create_task(watcher.run(), name="freshness-watcher"))
        _log.info("freshness_watcher_enabled", root=settings.local_repos_root)
    else:
        _log.info(
            "freshness_watcher_skipped",
            enabled=settings.freshness_watch_enabled,
            root=settings.local_repos_root,
        )
    return tasks


async def stop_freshness(tasks: list[asyncio.Task[None]]) -> None:
    """Cancel the freshness tasks and await their teardown."""
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # shutdown must always finish
            _log.warning("freshness_task_stop_error", error=str(exc))


__all__ = ["start_freshness", "stop_freshness"]
