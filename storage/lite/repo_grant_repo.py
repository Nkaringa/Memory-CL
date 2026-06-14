"""SQLite-backed repo_grants repo for lite mode.

Same surface as PostgresRepoGrantRepository. SQLite differences:
  - created_at uses TEXT + DEFAULT (datetime('now')) — no TIMESTAMPTZ, no now()
  - ISO TEXT timestamps are hydrated to datetime via _parse_dt
  - ANY(:array) is not available in SQLite; list_for_subjects builds the team
    IN clause dynamically with numbered bound parameters (:t0, :t1, ...) so
    no string values are ever interpolated into the SQL text.
  - When team_ids is empty, the team clause is omitted entirely (only the
    user clause fires), avoiding a vacuous `IN ()` which some SQLite builds
    reject.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.repo_grant_repo import RepoGrantRow

_DDL = """
CREATE TABLE IF NOT EXISTS repo_grants (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    repo_id      TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id   TEXT NOT NULL,
    access       TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (repo_id, subject_type, subject_id)
)
"""

_SELECT_COLS = "id, org_id, repo_id, subject_type, subject_id, access, created_at"


def _parse_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _row_to_grant(row: Any) -> RepoGrantRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return RepoGrantRow(
        id=m["id"],
        org_id=m["org_id"],
        repo_id=m["repo_id"],
        subject_type=m["subject_type"],
        subject_id=m["subject_id"],
        access=m["access"],
        created_at=_parse_dt(m["created_at"]),
    )


class SqliteRepoGrantRepository:
    """Concrete store for `repo_grants` over a SQLite AsyncEngine (lite mode)."""

    name: str = "sqlite_repo_grant_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
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
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO repo_grants (id, org_id, repo_id, subject_type, subject_id, access)"
                    " VALUES (:id, :org_id, :repo_id, :subject_type, :subject_id, :access)"
                    " ON CONFLICT (repo_id, subject_type, subject_id)"
                    " DO UPDATE SET access = excluded.access"
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
            ).first()
        if row is None:
            raise RuntimeError(
                f"repo_grant for ({repo_id!r}, {subject_type!r}, {subject_id!r}) vanished after upsert"
            )
        return _row_to_grant(row)

    async def get(self, id: str) -> RepoGrantRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_SELECT_COLS} FROM repo_grants WHERE id = :id"),
                    {"id": id},
                )
            ).first()
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
            ).all()
        return [_row_to_grant(r) for r in rows]

    async def list_for_subjects(
        self,
        *,
        org_id: str,
        user_id: str,
        team_ids: list[str],
    ) -> list[RepoGrantRow]:
        # SQLite has no ANY(:array); build an explicit IN clause instead.
        # All values are bound — no string interpolation of user data.
        params: dict[str, Any] = {"org": org_id, "uid": user_id}
        user_clause = "(subject_type = 'user' AND subject_id = :uid)"

        if team_ids:
            placeholders = ", ".join(f":t{i}" for i in range(len(team_ids)))
            for i, tid in enumerate(team_ids):
                params[f"t{i}"] = tid
            team_clause = f"(subject_type = 'team' AND subject_id IN ({placeholders}))"
            where_subjects = f"({user_clause} OR {team_clause})"
        else:
            where_subjects = user_clause

        sql = (
            f"SELECT {_SELECT_COLS} FROM repo_grants"
            f" WHERE org_id = :org AND {where_subjects}"
            " ORDER BY created_at"
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).all()
        return [_row_to_grant(r) for r in rows]

    async def revoke(self, id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM repo_grants WHERE id = :id"),
                {"id": id},
            )

    async def delete_for_repo(self, repo_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM repo_grants WHERE repo_id = :rid"),
                {"rid": repo_id},
            )


__all__ = ["SqliteRepoGrantRepository"]
