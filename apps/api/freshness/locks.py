"""Per-repo reingest locks shared by the poller and the watcher.

A managed repo could be polled while the filesystem watcher is also
reingesting it (e.g. a local checkout that's also git-managed). Serializing
on a per-repo lock means the two never run `run_ingest` for the same repo
concurrently — which would double-write the same Qdrant points and race the
unit reconciliation.
"""

from __future__ import annotations

import asyncio


class RepoLocks:
    """Lazily-created `asyncio.Lock` per repo_id (single event loop)."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, repo_id: str) -> asyncio.Lock:
        lock = self._locks.get(repo_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[repo_id] = lock
        return lock


__all__ = ["RepoLocks"]
