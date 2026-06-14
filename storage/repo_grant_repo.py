"""Per-repo access grants — one row per (repo_id, subject_type, subject_id).

A subject is either a direct user (`subject_type='user'`) or a team
(`subject_type='team'`).  Re-granting the same subject on the same repo
UPDATES the access level (UPSERT on the unique triple); the original row's
`id` is preserved.

B14/B15 reminder: plain INSERTs with no CTE wrapping do not need CAST on
TIMESTAMPTZ defaults — the DB applies now() directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.repo_grant_repo")

_DDL = """
CREATE TABLE IF NOT EXISTS repo_grants (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    repo_id      TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    access       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repo_id, subject_type, subject_id)
)
"""

_SELECT_COLS = "id, org_id, repo_id, subject_type, subject_id, access, created_at"


@dataclass(frozen=True, slots=True)
class RepoGrantRow:
    id: str
    org_id: str
    repo_id: str
    subject_type: str
    subject_id: str
    access: str
    created_at: datetime | None = None


def _row_to_grant(row: object) -> RepoGrantRow:
    m = row._mapping if hasattr(row, "_mapping") else row  # type: ignore[union-attr]
    return RepoGrantRow(
        id=m["id"],
        org_id=m["org_id"],
        repo_id=m["repo_id"],
        subject_type=m["subject_type"],
        subject_id=m["subject_id"],
        access=m["access"],
        created_at=m["created_at"],
    )


class PostgresRepoGrantRepository:
    """Concrete store for `repo_grants` over a Postgres AsyncEngine."""

    name: str = "repo_grant_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("repo_grant_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                await conn.execute(text(_DDL))

    async def grant(
        self,
        *,
        id: str,
        org_id: str,
        repo_id: str,
        subject_type: str,
        subject_id: str,
        access: str,
    ) -> RepoGrantRow:
        with _tracer.start_as_current_span("repo_grant_repo.grant"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO repo_grants (id, org_id, repo_id, subject_type, subject_id, access)"
                        " VALUES (:id, :org_id, :repo_id, :subject_type, :subject_id, :access)"
                        " ON CONFLICT (repo_id, subject_type, subject_id)"
                        " DO UPDATE SET access = EXCLUDED.access"
                    ),
                    {
                        "id": id,
                        "org_id": org_id,
                        "repo_id": repo_id,
                        "subject_type": subject_type,
                        "subject_id": subject_id,
                        "access": access,
                    },
                )
        # Read back the effective row — on conflict the original id is kept.
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        f"SELECT {_SELECT_COLS} FROM repo_grants"
                        " WHERE repo_id = :repo_id AND subject_type = :subject_type AND subject_id = :subject_id"
                    ),
                    {"repo_id": repo_id, "subject_type": subject_type, "subject_id": subject_id},
                )
            ).mappings().first()
        if row is None:
            raise RuntimeError(f"repo_grant for ({repo_id!r}, {subject_type!r}, {subject_id!r}) vanished after upsert")
        return _row_to_grant(row)

    async def get(self, id: str) -> RepoGrantRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_SELECT_COLS} FROM repo_grants WHERE id = :id"),
                    {"id": id},
                )
            ).mappings().first()
        return _row_to_grant(row) if row else None

    async def list_for_repo(self, *, repo_id: str) -> list[RepoGrantRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT {_SELECT_COLS} FROM repo_grants"
                        " WHERE repo_id = :repo_id ORDER BY created_at"
                    ),
                    {"repo_id": repo_id},
                )
            ).mappings().all()
        return [_row_to_grant(r) for r in rows]

    async def list_for_subjects(
        self,
        *,
        org_id: str,
        user_id: str,
        team_ids: list[str],
    ) -> list[RepoGrantRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT {_SELECT_COLS} FROM repo_grants"
                        " WHERE org_id = :org AND ("
                        "  (subject_type = 'user' AND subject_id = :uid)"
                        "  OR (subject_type = 'team' AND subject_id = ANY(:tids))"
                        " ) ORDER BY created_at"
                    ),
                    {"org": org_id, "uid": user_id, "tids": team_ids},
                )
            ).mappings().all()
        return [_row_to_grant(r) for r in rows]

    async def revoke(self, id: str) -> None:
        with _tracer.start_as_current_span("repo_grant_repo.revoke"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM repo_grants WHERE id = :id"),
                    {"id": id},
                )

    async def delete_for_repo(self, repo_id: str) -> None:
        with _tracer.start_as_current_span("repo_grant_repo.delete_for_repo"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM repo_grants WHERE repo_id = :rid"),
                    {"rid": repo_id},
                )


__all__ = ["RepoGrantRow", "PostgresRepoGrantRepository"]
