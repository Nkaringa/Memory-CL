"""Background poll loop that keeps managed repos fresh.

Every `interval_seconds` it walks the watch-enabled managed repos and syncs
any whose tracked branch moved (via `sync_managed_repo`). Serialized per
repo against the watcher through the shared `RepoLocks`. Designed to run as
a lifespan background task: `run()` loops until cancelled and never lets a
single repo's failure stop the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from apps.api.freshness.git import GitRunner
from apps.api.freshness.locks import RepoLocks
from apps.api.freshness.managed import IngestFn, SyncResult, sync_managed_repo
from core.logging import get_logger
from storage.repo_registry_repo import RepoRegistryRepository

_log = get_logger(__name__)


class FreshnessPoller:
    """Polls managed repos and reingests on change."""

    def __init__(
        self,
        *,
        registry: RepoRegistryRepository,
        git: GitRunner,
        ingest: IngestFn,
        locks: RepoLocks,
        interval_seconds: float,
        safe_mode_active: Callable[[], bool] | None = None,
    ) -> None:
        self._registry = registry
        self._git = git
        self._ingest = ingest
        self._locks = locks
        self._interval = max(1.0, interval_seconds)
        self._safe_mode_active = safe_mode_active

    async def poll_all(self) -> list[SyncResult]:
        """One pass over every watch-enabled managed repo. Skips under safe
        mode. Each repo is synced under its per-repo lock; failures are
        captured as a `SyncResult` (with `error`), never raised."""
        if self._safe_mode_active is not None and self._safe_mode_active():
            return []
        repos = [
            r
            for r in await self._registry.list_watched()
            if r.source_type == "managed"
        ]
        results: list[SyncResult] = []
        for repo in repos:
            async with self._locks.get(repo.repo_id):
                try:
                    res = await sync_managed_repo(
                        repo,
                        registry=self._registry,
                        git=self._git,
                        ingest=self._ingest,
                    )
                except Exception as exc:
                    _log.warning(
                        "freshness_poll_repo_failed",
                        repo_id=repo.repo_id,
                        error=str(exc),
                    )
                    res = SyncResult(repo.repo_id, changed=False, error=str(exc))
            results.append(res)
            if res.changed and res.error is None:
                _log.info(
                    "freshness_managed_reingested",
                    repo_id=repo.repo_id,
                    commit_sha=res.new_sha,
                )
        return results

    async def run(self) -> None:
        """Loop forever (until cancelled): poll, then sleep the interval.

        Polls immediately on start so a repo that drifted while the service
        was down is reconciled at boot, not one interval later.
        """
        _log.info("freshness_poller_started", interval_seconds=self._interval)
        try:
            while True:
                try:
                    await self.poll_all()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _log.warning("freshness_poll_pass_failed", error=str(exc))
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            _log.info("freshness_poller_stopped")
            raise


__all__ = ["FreshnessPoller"]
