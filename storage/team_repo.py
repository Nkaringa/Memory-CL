"""Teams and team memberships — sub-org groupings with membership.

Two tables in one repository:
  - `teams`: one row per (org_id, slug) team.
  - `team_memberships`: one row per (team_id, user_id) pairing.

UNIQUE(org_id, slug) is enforced at the DB level and bubbles up as an
IntegrityError on duplicate slug within the same org.
add_team_member is idempotent via ON CONFLICT DO NOTHING.

B14/B15 reminder: plain INSERTs with no CTE wrapping do not need CAST on
TIMESTAMPTZ defaults — the DB applies now() directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.team_repo")

_DDL_TEAMS = """
CREATE TABLE IF NOT EXISTS teams (
    team_id    TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, slug)
)
"""

_DDL_TEAM_MEMBERSHIPS = """
CREATE TABLE IF NOT EXISTS team_memberships (
    team_id    TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, user_id)
)
"""

_SELECT_TEAM_COLS = "team_id, org_id, name, slug, created_at"


@dataclass(frozen=True, slots=True)
class TeamRow:
    team_id: str
    org_id: str
    name: str
    slug: str
    created_at: datetime | None = None


def _row_to_team(row: object) -> TeamRow:
    m = row._mapping if hasattr(row, "_mapping") else row  # type: ignore[union-attr]
    return TeamRow(
        team_id=m["team_id"],
        org_id=m["org_id"],
        name=m["name"],
        slug=m["slug"],
        created_at=m["created_at"],
    )


class PostgresTeamRepository:
    """Concrete store for `teams` + `team_memberships` over a Postgres AsyncEngine."""

    name: str = "team_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("team_repo.ensure_schema"):
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
        with _tracer.start_as_current_span("team_repo.create_team"):
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
            ).mappings().first()
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
            ).mappings().all()
        return [_row_to_team(r) for r in rows]

    async def delete_team(self, team_id: str) -> None:
        with _tracer.start_as_current_span("team_repo.delete_team"):
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
        with _tracer.start_as_current_span("team_repo.add_team_member"):
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
        with _tracer.start_as_current_span("team_repo.remove_team_member"):
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
            ).mappings().all()
        return [r["user_id"] for r in rows]

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
            ).mappings().all()
        return [r["team_id"] for r in rows]


__all__ = ["TeamRow", "PostgresTeamRepository"]
