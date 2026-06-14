"""Durable membership store — user ↔ org associations with roles.

One `memberships` row per (user_id, org_id) pair.  The UNIQUE constraint
on (user_id, org_id) enforces the single-membership-per-org-per-user rule
at the DB level.

B14/B15 reminder: TIMESTAMPTZ binds inside CTE-shaped statements need an
explicit CAST.  The INSERT here is plain (no CTE), so no cast is needed for
the default `now()` column.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.membership_repo")

_DDL = """
CREATE TABLE IF NOT EXISTS memberships (
    membership_id TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    org_id        TEXT NOT NULL,
    role          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, org_id)
)
"""


@dataclass(frozen=True, slots=True)
class MembershipRow:
    membership_id: str
    user_id: str
    org_id: str
    role: str
    status: str = "active"
    created_at: datetime | None = None


def _row_to_membership(row: object) -> MembershipRow:
    m = row._mapping if hasattr(row, "_mapping") else row  # type: ignore[union-attr]
    return MembershipRow(
        membership_id=m["membership_id"],
        user_id=m["user_id"],
        org_id=m["org_id"],
        role=m["role"],
        status=m["status"],
        created_at=m["created_at"],
    )


class PostgresMembershipRepository:
    """Concrete store for `memberships` over a Postgres AsyncEngine."""

    name: str = "membership_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("membership_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                await conn.execute(text(_DDL))

    async def add_member(
        self,
        *,
        membership_id: str,
        user_id: str,
        org_id: str,
        role: str,
        status: str = "active",
    ) -> MembershipRow:
        with _tracer.start_as_current_span("membership_repo.add_member"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO memberships (membership_id, user_id, org_id, role, status)"
                        " VALUES (:mid, :uid, :oid, :role, :status)"
                    ),
                    {"mid": membership_id, "uid": user_id, "oid": org_id, "role": role, "status": status},
                )
        got = await self.get_membership(user_id=user_id, org_id=org_id)
        if got is None:
            raise RuntimeError(f"membership ({user_id!r}, {org_id!r}) vanished after insert")
        return got

    async def get_membership(self, *, user_id: str, org_id: str) -> MembershipRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT membership_id, user_id, org_id, role, status, created_at"
                        " FROM memberships WHERE user_id = :uid AND org_id = :oid"
                    ),
                    {"uid": user_id, "oid": org_id},
                )
            ).mappings().first()
        return _row_to_membership(row) if row else None

    async def list_orgs_for_user(self, user_id: str) -> list[MembershipRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT membership_id, user_id, org_id, role, status, created_at"
                        " FROM memberships WHERE user_id = :uid ORDER BY created_at"
                    ),
                    {"uid": user_id},
                )
            ).mappings().all()
        return [_row_to_membership(r) for r in rows]

    async def list_members(self, *, org_id: str) -> list[MembershipRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT membership_id, user_id, org_id, role, status, created_at"
                        " FROM memberships WHERE org_id = :oid ORDER BY created_at"
                    ),
                    {"oid": org_id},
                )
            ).mappings().all()
        return [_row_to_membership(r) for r in rows]

    async def set_role(self, *, user_id: str, org_id: str, role: str) -> None:
        with _tracer.start_as_current_span("membership_repo.set_role"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE memberships SET role = :role"
                        " WHERE user_id = :uid AND org_id = :oid"
                    ),
                    {"role": role, "uid": user_id, "oid": org_id},
                )


__all__ = ["MembershipRow", "PostgresMembershipRepository"]
