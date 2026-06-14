"""SQLite-backed teams + team_memberships repo for lite mode.

Same surface as PostgresTeamRepository. SQLite differences:
  - created_at uses TEXT + DEFAULT (datetime('now')) — no TIMESTAMPTZ, no now()
  - ISO TEXT timestamps are hydrated to datetime via _parse_dt (same helper
    pattern as storage/lite/membership_repo.py and storage/lite/org_repo.py)
  - UNIQUE(org_id, slug) raises IntegrityError on duplicates — same semantics as PG
  - ON CONFLICT (team_id, user_id) DO NOTHING works natively in SQLite
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.team_repo import TeamRow

_DDL_TEAMS = """
CREATE TABLE IF NOT EXISTS teams (
    team_id    TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (org_id, slug)
)
"""

_DDL_TEAM_MEMBERSHIPS = """
CREATE TABLE IF NOT EXISTS team_memberships (
    team_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (team_id, user_id)
)
"""

_SELECT_TEAM_COLS = "team_id, org_id, name, slug, created_at"


def _parse_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _row_to_team(row: Any) -> TeamRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return TeamRow(
        team_id=m["team_id"],
        org_id=m["org_id"],
        name=m["name"],
        slug=m["slug"],
        created_at=_parse_dt(m["created_at"]),
    )


class SqliteTeamRepository:
    """Concrete store for `teams` + `team_memberships` over a SQLite AsyncEngine."""

    name: str = "sqlite_team_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL_TEAMS))
            await conn.execute(text(_DDL_TEAM_MEMBERSHIPS))

    async def create_team(
        self,
        *,
        team_id: str,
        org_id: str,
        name: str,
        slug: str,
    ) -> TeamRow:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO teams (team_id, org_id, name, slug)"
                    " VALUES (:team_id, :org_id, :name, :slug)"
                ),
                {"team_id": team_id, "org_id": org_id, "name": name, "slug": slug},
            )
        got = await self.get_team(team_id)
        if got is None:
            raise RuntimeError(f"team {team_id!r} vanished after insert")
        return got

    async def get_team(self, team_id: str) -> TeamRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_SELECT_TEAM_COLS} FROM teams WHERE team_id = :team_id"),
                    {"team_id": team_id},
                )
            ).first()
        return _row_to_team(row) if row else None

    async def list_teams(self, *, org_id: str) -> list[TeamRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT {_SELECT_TEAM_COLS} FROM teams"
                        " WHERE org_id = :org_id ORDER BY created_at"
                    ),
                    {"org_id": org_id},
                )
            ).all()
        return [_row_to_team(r) for r in rows]

    async def delete_team(self, team_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM team_memberships WHERE team_id = :team_id"),
                {"team_id": team_id},
            )
            await conn.execute(
                text("DELETE FROM teams WHERE team_id = :team_id"),
                {"team_id": team_id},
            )

    async def add_team_member(self, *, team_id: str, user_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO team_memberships (team_id, user_id)"
                    " VALUES (:team_id, :user_id)"
                    " ON CONFLICT (team_id, user_id) DO NOTHING"
                ),
                {"team_id": team_id, "user_id": user_id},
            )

    async def remove_team_member(self, *, team_id: str, user_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM team_memberships"
                    " WHERE team_id = :team_id AND user_id = :user_id"
                ),
                {"team_id": team_id, "user_id": user_id},
            )

    async def list_team_member_ids(self, team_id: str) -> list[str]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT user_id FROM team_memberships"
                        " WHERE team_id = :team_id ORDER BY created_at"
                    ),
                    {"team_id": team_id},
                )
            ).all()
        return [r[0] for r in rows]

    async def team_ids_for_user(self, *, user_id: str, org_id: str) -> list[str]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT tm.team_id FROM team_memberships tm"
                        " JOIN teams t ON t.team_id = tm.team_id"
                        " WHERE tm.user_id = :user_id AND t.org_id = :org_id"
                        " ORDER BY tm.created_at"
                    ),
                    {"user_id": user_id, "org_id": org_id},
                )
            ).all()
        return [r[0] for r in rows]


__all__ = ["SqliteTeamRepository"]
