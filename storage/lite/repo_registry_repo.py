"""SQLite-backed repo_registry for lite mode (Phase-3 freshness).

Same surface + RepoRegistryRow as the Postgres version. SQLite flavor:
BOOLEAN -> INTEGER, TIMESTAMPTZ -> ISO TEXT, no casts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.repo_registry_repo import RepoRegistryRow

_DDL = """
CREATE TABLE IF NOT EXISTS repo_registry (
    repo_id         TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL DEFAULT 'default',
    source_type     TEXT NOT NULL DEFAULT 'local',
    repo_path       TEXT NOT NULL,
    remote_url      TEXT,
    branch          TEXT,
    last_commit_sha TEXT,
    watch_enabled   INTEGER NOT NULL DEFAULT 1,
    last_synced_at  TEXT,
    last_change_at  TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
)
"""

# Defensive migration for pre-existing SQLite files — SQLite lacks
# ADD COLUMN IF NOT EXISTS, so we catch the "duplicate column" error.
_MIGRATE_ORG_ID = "ALTER TABLE repo_registry ADD COLUMN org_id TEXT NOT NULL DEFAULT 'default'"

_UPSERT_LOCAL = text("""
INSERT INTO repo_registry (
    repo_id, org_id, source_type, repo_path, last_commit_sha,
    last_synced_at, created_at, updated_at
) VALUES (:repo_id, :org_id, 'local', :repo_path, :last_commit_sha, :ts, :ts, :ts)
ON CONFLICT(repo_id) DO UPDATE SET
    org_id          = excluded.org_id,
    repo_path       = excluded.repo_path,
    last_commit_sha = excluded.last_commit_sha,
    last_synced_at  = excluded.last_synced_at,
    last_error      = NULL,
    updated_at      = excluded.updated_at
""")

_UPSERT_MANAGED = text("""
INSERT INTO repo_registry (
    repo_id, org_id, source_type, repo_path, remote_url, branch,
    last_commit_sha, last_synced_at, created_at, updated_at
) VALUES (:repo_id, :org_id, 'managed', :repo_path, :remote_url, :branch,
          :last_commit_sha, :ts, :ts, :ts)
ON CONFLICT(repo_id) DO UPDATE SET
    org_id          = excluded.org_id,
    source_type     = 'managed',
    repo_path       = excluded.repo_path,
    remote_url      = excluded.remote_url,
    branch          = excluded.branch,
    last_commit_sha = excluded.last_commit_sha,
    last_synced_at  = excluded.last_synced_at,
    last_error      = NULL,
    updated_at      = excluded.updated_at
""")

_SELECT_ALL = text("SELECT * FROM repo_registry ORDER BY repo_id")
_SELECT_ONE = text("SELECT * FROM repo_registry WHERE repo_id = :repo_id")
_DELETE = text("DELETE FROM repo_registry WHERE repo_id = :repo_id")
_MARK_SYNCED = text(
    "UPDATE repo_registry SET last_commit_sha = :last_commit_sha, "
    "last_synced_at = :ts, last_error = NULL, updated_at = :ts WHERE repo_id = :repo_id"
)
_MARK_CHANGE = text(
    "UPDATE repo_registry SET last_change_at = :ts, updated_at = :ts WHERE repo_id = :repo_id"
)
_MARK_ERROR = text(
    "UPDATE repo_registry SET last_error = :last_error, updated_at = :ts WHERE repo_id = :repo_id"
)
_SET_WATCH = text(
    "UPDATE repo_registry SET watch_enabled = :watch_enabled, updated_at = :ts "
    "WHERE repo_id = :repo_id"
)


def _dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _row(row: Any) -> RepoRegistryRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return RepoRegistryRow(
        repo_id=m["repo_id"],
        source_type=m["source_type"], repo_path=m["repo_path"],
        remote_url=m["remote_url"], branch=m["branch"], last_commit_sha=m["last_commit_sha"],
        watch_enabled=bool(m["watch_enabled"]), last_synced_at=_dt(m["last_synced_at"]),
        last_change_at=_dt(m["last_change_at"]), last_error=m["last_error"],
        created_at=_dt(m["created_at"]), updated_at=_dt(m["updated_at"]),
        org_id=m["org_id"],
    )


class SqliteRepoRegistryRepository:
    name: str = "sqlite_repo_registry_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL))
            # Defensive migration for pre-existing SQLite files that lack org_id.
            # SQLite has no ADD COLUMN IF NOT EXISTS, so we catch the duplicate-column error.
            try:
                await conn.execute(text(_MIGRATE_ORG_ID))
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    async def list_all(self) -> list[RepoRegistryRow]:
        async with self._engine.connect() as conn:
            return [_row(r) for r in (await conn.execute(_SELECT_ALL)).fetchall()]

    async def list_watched(self) -> list[RepoRegistryRow]:
        return [r for r in await self.list_all() if r.watch_enabled]

    async def get(self, repo_id: str) -> RepoRegistryRow | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(_SELECT_ONE, {"repo_id": repo_id})).first()
            return _row(row) if row else None

    async def upsert_local(
        self, repo_id: str, repo_path: str, commit_sha: str | None,
        org_id: str = "default",
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_UPSERT_LOCAL, {
                "repo_id": repo_id, "org_id": org_id, "repo_path": repo_path,
                "last_commit_sha": commit_sha, "ts": datetime.now(UTC).isoformat(),
            })

    async def add_managed(
        self, repo_id: str, repo_path: str, remote_url: str,
        branch: str | None, commit_sha: str | None,
        org_id: str = "default",
    ) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_UPSERT_MANAGED, {
                "repo_id": repo_id, "org_id": org_id, "repo_path": repo_path,
                "remote_url": remote_url, "branch": branch, "last_commit_sha": commit_sha,
                "ts": datetime.now(UTC).isoformat(),
            })

    async def mark_synced(self, repo_id: str, commit_sha: str | None) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_MARK_SYNCED, {
                "repo_id": repo_id, "last_commit_sha": commit_sha,
                "ts": datetime.now(UTC).isoformat(),
            })

    async def mark_change(self, repo_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                _MARK_CHANGE, {"repo_id": repo_id, "ts": datetime.now(UTC).isoformat()}
            )

    async def mark_error(self, repo_id: str, message: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_MARK_ERROR, {
                "repo_id": repo_id, "last_error": message[:2000],
                "ts": datetime.now(UTC).isoformat(),
            })

    async def set_watch_enabled(self, repo_id: str, enabled: bool) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_SET_WATCH, {
                "repo_id": repo_id, "watch_enabled": int(enabled),
                "ts": datetime.now(UTC).isoformat(),
            })

    async def delete(self, repo_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_DELETE, {"repo_id": repo_id})


__all__ = ["SqliteRepoRegistryRepository"]
