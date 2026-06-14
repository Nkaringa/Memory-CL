"""SQLite-backed memberships repo for lite mode.

Same surface as PostgresMembershipRepository.  SQLite differences:
  - created_at uses TEXT + DEFAULT (datetime('now')) — no TIMESTAMPTZ, no now()
  - ISO TEXT timestamps are hydrated to datetime via _parse_dt (same helper
    pattern as storage/lite/app_config_repo.py and storage/lite/org_repo.py)
  - UNIQUE(user_id, org_id) raises IntegrityError on duplicates — same semantics as PG
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.membership_repo import MembershipRow

_DDL = """
CREATE TABLE IF NOT EXISTS memberships (
    membership_id TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    org_id        TEXT NOT NULL,
    role          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, org_id)
)
"""


def _parse_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _row_to_membership(row: Any) -> MembershipRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return MembershipRow(
        membership_id=m["membership_id"],
        user_id=m["user_id"],
        org_id=m["org_id"],
        role=m["role"],
        status=m["status"],
        created_at=_parse_dt(m["created_at"]),
    )


class SqliteMembershipRepository:
    """Concrete store for `memberships` over a SQLite AsyncEngine."""

    name: str = "sqlite_membership_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
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
            ).first()
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
            ).all()
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
            ).all()
        return [_row_to_membership(r) for r in rows]

    async def set_role(self, *, user_id: str, org_id: str, role: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE memberships SET role = :role"
                    " WHERE user_id = :uid AND org_id = :oid"
                ),
                {"role": role, "uid": user_id, "oid": org_id},
            )

    async def remove_member(self, *, user_id: str, org_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM memberships WHERE user_id = :uid AND org_id = :oid"),
                {"uid": user_id, "oid": org_id},
            )


__all__ = ["SqliteMembershipRepository"]
