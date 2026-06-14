"""Durable repo registry — the persistent record of every known repo.

Freshness (Phase 3) needs to know WHAT to keep fresh and WHERE the code
lives, which nothing persisted before (`repo_path` was transient to a
single `/ingest` call). Each row is one repo:

  * **local**  — code already on a mounted path (`/repos/<name>`); the
    engine just reads it. Kept fresh by the filesystem watcher.
  * **managed** — a git URL Memory-CL cloned into a writable workspace
    (`/managed/<repo_id>`) and keeps pulled. Kept fresh by polling.

SQL lives only here; the engine is injected (the same lazy `engine_proxy`
the lifespan uses), and `ensure_schema()` is idempotent — it runs in the
same bootstrap pass as `ingestion_units` / `app_config`.

B14/B15 reminder: every non-TEXT bind inside a CTE-shaped statement needs
an explicit CAST (asyncpg can't infer the column type through the WITH
wrapper). B16 reminder: no ":name"-style tokens in comments inside a
text() string or SQLAlchemy treats them as required binds.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.repo_registry_repo")

SourceType = str  # 'local' | 'managed'


_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS repo_registry (
        repo_id         TEXT PRIMARY KEY,
        source_type     TEXT NOT NULL DEFAULT 'local',
        repo_path       TEXT NOT NULL,
        remote_url      TEXT,
        branch          TEXT,
        last_commit_sha TEXT,
        watch_enabled   BOOLEAN NOT NULL DEFAULT true,
        last_synced_at  TIMESTAMPTZ,
        last_change_at  TIMESTAMPTZ,
        last_error      TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at      TIMESTAMPTZ
    )
    """,
)


@dataclass(frozen=True, slots=True)
class RepoRegistryRow:
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
    created_at: datetime | None
    updated_at: datetime | None


# Register/refresh a LOCAL repo (called after a successful /ingest). On
# conflict we touch only the path + sync fields — never source_type,
# watch_enabled, remote_url, or branch — so a managed repo that happens to
# be re-ingested keeps its managed identity and the user's pause setting.
_UPSERT_LOCAL = text("""
WITH input AS (
    SELECT
        :repo_id AS repo_id,
        :repo_path AS repo_path,
        :last_commit_sha AS last_commit_sha,
        CAST(:ts AS TIMESTAMPTZ) AS ts
)
INSERT INTO repo_registry (
    repo_id, source_type, repo_path, last_commit_sha,
    last_synced_at, created_at, updated_at
) SELECT
    repo_id, 'local', repo_path, last_commit_sha, ts, ts, ts
  FROM input
ON CONFLICT (repo_id) DO UPDATE SET
    repo_path       = EXCLUDED.repo_path,
    last_commit_sha = EXCLUDED.last_commit_sha,
    last_synced_at  = EXCLUDED.last_synced_at,
    last_error      = NULL,
    updated_at      = EXCLUDED.updated_at
""")


# Register/refresh a MANAGED repo (called at clone/add time).
_UPSERT_MANAGED = text("""
WITH input AS (
    SELECT
        :repo_id AS repo_id,
        :repo_path AS repo_path,
        :remote_url AS remote_url,
        :branch AS branch,
        :last_commit_sha AS last_commit_sha,
        CAST(:ts AS TIMESTAMPTZ) AS ts
)
INSERT INTO repo_registry (
    repo_id, source_type, repo_path, remote_url, branch,
    last_commit_sha, last_synced_at, created_at, updated_at
) SELECT
    repo_id, 'managed', repo_path, remote_url, branch,
    last_commit_sha, ts, ts, ts
  FROM input
ON CONFLICT (repo_id) DO UPDATE SET
    source_type     = 'managed',
    repo_path       = EXCLUDED.repo_path,
    remote_url      = EXCLUDED.remote_url,
    branch          = EXCLUDED.branch,
    last_commit_sha = EXCLUDED.last_commit_sha,
    last_synced_at  = EXCLUDED.last_synced_at,
    last_error      = NULL,
    updated_at      = EXCLUDED.updated_at
""")

_SELECT_ALL = text("SELECT * FROM repo_registry ORDER BY repo_id")
_SELECT_ONE = text("SELECT * FROM repo_registry WHERE repo_id = :repo_id")
_DELETE = text("DELETE FROM repo_registry WHERE repo_id = :repo_id")

_MARK_SYNCED = text("""
UPDATE repo_registry SET
    last_commit_sha = :last_commit_sha,
    last_synced_at  = CAST(:ts AS TIMESTAMPTZ),
    last_error      = NULL,
    updated_at      = CAST(:ts AS TIMESTAMPTZ)
WHERE repo_id = :repo_id
""")

_MARK_CHANGE = text("""
UPDATE repo_registry SET
    last_change_at = CAST(:ts AS TIMESTAMPTZ),
    updated_at     = CAST(:ts AS TIMESTAMPTZ)
WHERE repo_id = :repo_id
""")

_MARK_ERROR = text("""
UPDATE repo_registry SET
    last_error = :last_error,
    updated_at = CAST(:ts AS TIMESTAMPTZ)
WHERE repo_id = :repo_id
""")

_SET_WATCH = text("""
UPDATE repo_registry SET
    watch_enabled = CAST(:watch_enabled AS BOOLEAN),
    updated_at    = CAST(:ts AS TIMESTAMPTZ)
WHERE repo_id = :repo_id
""")


def _row_to_registry(row: Any) -> RepoRegistryRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return RepoRegistryRow(
        repo_id=m["repo_id"],
        source_type=m["source_type"],
        repo_path=m["repo_path"],
        remote_url=m["remote_url"],
        branch=m["branch"],
        last_commit_sha=m["last_commit_sha"],
        watch_enabled=bool(m["watch_enabled"]),
        last_synced_at=m["last_synced_at"],
        last_change_at=m["last_change_at"],
        last_error=m["last_error"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


class RepoRegistryRepository:
    """Concrete store for `repo_registry` over the injected AsyncEngine.

    Mirrors `AppConfigRepository` / `PostgresIngestionRepository`: SQL is
    confined here, `ensure_schema()` is idempotent, timestamps are stamped
    server-side in UTC.
    """

    name: str = "repo_registry_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ----- Bootstrap -----
    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("repo_registry_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                for stmt in _DDL_STATEMENTS:
                    await conn.execute(text(stmt))

    # ----- Reads -----
    async def list_all(self) -> list[RepoRegistryRow]:
        async with self._engine.connect() as conn:
            result = await conn.execute(_SELECT_ALL)
            return [_row_to_registry(r) for r in result.fetchall()]

    async def list_watched(self) -> list[RepoRegistryRow]:
        """Rows the freshness loops should act on (watch_enabled = true)."""
        return [r for r in await self.list_all() if r.watch_enabled]

    async def get(self, repo_id: str) -> RepoRegistryRow | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(_SELECT_ONE, {"repo_id": repo_id})
            row = result.first()
            return _row_to_registry(row) if row else None

    # ----- Writes -----
    async def upsert_local(
        self, repo_id: str, repo_path: str, commit_sha: str | None
    ) -> None:
        params = {
            "repo_id": repo_id,
            "repo_path": repo_path,
            "last_commit_sha": commit_sha,
            "ts": datetime.now(UTC),
        }
        with _tracer.start_as_current_span("repo_registry_repo.upsert_local"):
            async with self._engine.begin() as conn:
                await conn.execute(_UPSERT_LOCAL, params)

    async def add_managed(
        self,
        repo_id: str,
        repo_path: str,
        remote_url: str,
        branch: str | None,
        commit_sha: str | None,
    ) -> None:
        params = {
            "repo_id": repo_id,
            "repo_path": repo_path,
            "remote_url": remote_url,
            "branch": branch,
            "last_commit_sha": commit_sha,
            "ts": datetime.now(UTC),
        }
        with _tracer.start_as_current_span("repo_registry_repo.add_managed"):
            async with self._engine.begin() as conn:
                await conn.execute(_UPSERT_MANAGED, params)

    async def mark_synced(self, repo_id: str, commit_sha: str | None) -> None:
        params = {
            "repo_id": repo_id,
            "last_commit_sha": commit_sha,
            "ts": datetime.now(UTC),
        }
        async with self._engine.begin() as conn:
            await conn.execute(_MARK_SYNCED, params)

    async def mark_change(self, repo_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                _MARK_CHANGE, {"repo_id": repo_id, "ts": datetime.now(UTC)}
            )

    async def mark_error(self, repo_id: str, message: str) -> None:
        params = {
            "repo_id": repo_id,
            # Bound the stored error so a giant git/stack message can't
            # bloat the row.
            "last_error": message[:2000],
            "ts": datetime.now(UTC),
        }
        async with self._engine.begin() as conn:
            await conn.execute(_MARK_ERROR, params)

    async def set_watch_enabled(self, repo_id: str, enabled: bool) -> None:
        params = {
            "repo_id": repo_id,
            "watch_enabled": enabled,
            "ts": datetime.now(UTC),
        }
        async with self._engine.begin() as conn:
            await conn.execute(_SET_WATCH, params)

    async def delete(self, repo_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_DELETE, {"repo_id": repo_id})


__all__: Sequence[str] = ["RepoRegistryRepository", "RepoRegistryRow"]
