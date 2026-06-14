"""Filesystem watcher that keeps LOCAL repos fresh.

Local repos are code already on a mounted path (`/repos/<name>`); the user
/ CI / a mounted volume puts it there. This watcher uses `watchfiles`
(already a dependency via uvicorn[standard]) to notice file changes under
the local-repos root, debounces a burst, maps each changed path back to its
registered repo, and reingests.

The `awatch` source is injected (`awatch_factory`) so the mapping +
reingest logic is unit-tested against a canned change stream — no real
filesystem events, no flakiness.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from apps.api.freshness.git import GitError, GitRunner
from apps.api.freshness.locks import RepoLocks
from apps.api.freshness.managed import IngestFn
from core.logging import get_logger
from storage.repo_registry_repo import RepoRegistryRepository

_log = get_logger(__name__)

# Directory names whose churn must never trigger a reingest — VCS metadata,
# dependency/vendor trees, build output. A single git operation rewrites
# hundreds of `.git/` files; without this the watcher would storm.
_IGNORE_SEGMENTS = frozenset({
    ".git", "node_modules", "dist", "build", "target",
    ".venv", "venv", "__pycache__", ".next", ".mypy_cache", ".pytest_cache",
})


def change_is_relevant(path: str) -> bool:
    """True when a changed path is real source (not in an ignored dir)."""
    parts = path.replace("\\", "/").split("/")
    return _IGNORE_SEGMENTS.isdisjoint(parts)


# A factory that yields debounced batches of changed absolute paths. The
# production binding wraps `watchfiles.awatch`; tests pass a fake.
AwatchFactory = Callable[..., AsyncIterator[set[tuple[Any, str]]]]


class FreshnessWatcher:
    """Reingests local repos when their files change on disk."""

    def __init__(
        self,
        *,
        registry: RepoRegistryRepository,
        ingest: IngestFn,
        locks: RepoLocks,
        watch_root: str,
        awatch_factory: AwatchFactory,
        git: GitRunner | None = None,
        safe_mode_active: Callable[[], bool] | None = None,
        debounce_ms: int = 3000,
        force_polling: bool = False,
    ) -> None:
        self._registry = registry
        self._ingest = ingest
        self._locks = locks
        self._watch_root = watch_root
        self._awatch_factory = awatch_factory
        self._git = git
        self._safe_mode_active = safe_mode_active
        self._debounce_ms = debounce_ms
        self._force_polling = force_polling

    async def _resolve_repo_ids(self, changed_paths: set[str]) -> set[str]:
        """Map changed paths to local repo_ids by longest path-prefix match."""
        locals_ = [
            r
            for r in await self._registry.list_watched()
            if r.source_type == "local"
        ]
        hits: set[str] = set()
        for path in changed_paths:
            best_id: str | None = None
            best_len = -1
            for r in locals_:
                root = r.repo_path.rstrip("/")
                if (path == root or path.startswith(root + "/")) and len(root) > best_len:
                    best_id, best_len = r.repo_id, len(root)
            if best_id is not None:
                hits.add(best_id)
        return hits

    async def _commit_sha(self, repo_path: str) -> str:
        """Best-effort current HEAD for provenance; 'auto' if not a git repo."""
        if self._git is None:
            return "auto"
        try:
            return await self._git.head_sha(repo_path)
        except GitError:
            return "auto"

    async def _reingest(self, repo_id: str) -> bool:
        repo = await self._registry.get(repo_id)
        if repo is None or not repo.watch_enabled:
            return False
        sha = await self._commit_sha(repo.repo_path)
        async with self._locks.get(repo_id):
            await self._registry.mark_change(repo_id)
            try:
                await self._ingest(
                    repo_id=repo_id, repo_path=repo.repo_path, commit_sha=sha
                )
            except Exception as exc:
                await self._registry.mark_error(repo_id, str(exc))
                _log.warning(
                    "freshness_watch_reingest_failed", repo_id=repo_id, error=str(exc)
                )
                return False
            await self._registry.mark_synced(repo_id, sha)
        _log.info("freshness_local_reingested", repo_id=repo_id, commit_sha=sha)
        return True

    async def handle_batch(self, changed_paths: set[str]) -> list[str]:
        """Process one debounced batch: map to repos, reingest each. Skips
        under safe mode. Returns the repo_ids that were reingested."""
        if self._safe_mode_active is not None and self._safe_mode_active():
            return []
        relevant = {p for p in changed_paths if change_is_relevant(p)}
        if not relevant:
            return []
        repo_ids = await self._resolve_repo_ids(relevant)
        done: list[str] = []
        for repo_id in sorted(repo_ids):
            if await self._reingest(repo_id):
                done.append(repo_id)
        return done

    async def run(self) -> None:
        """Watch the local-repos root until cancelled. Each debounced batch
        is handled sequentially, so a change arriving mid-reingest is simply
        picked up in the next batch (no explicit dirty flag needed)."""
        _log.info(
            "freshness_watcher_started",
            watch_root=self._watch_root,
            debounce_ms=self._debounce_ms,
        )
        async for changes in self._awatch_factory(
            self._watch_root,
            watch_filter=lambda _change, path: change_is_relevant(path),
            debounce=self._debounce_ms,
            force_polling=self._force_polling,
        ):
            paths = {path for _change, path in changes}
            try:
                await self.handle_batch(paths)
            except Exception as exc:
                _log.warning("freshness_watch_batch_failed", error=str(exc))


__all__ = ["FreshnessWatcher", "change_is_relevant"]
