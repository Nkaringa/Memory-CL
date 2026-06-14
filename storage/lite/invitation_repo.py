"""SQLite-backed invitation repo for lite mode.

Same surface as PostgresInvitationRepository. SQLite deviations:
  - Timestamp columns are stored as INTEGER epoch seconds (not TIMESTAMPTZ):
      created_at  INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
      expires_at  INTEGER NOT NULL
  - Expiry comparison uses a Python-computed epoch bound (:now) rather than
    SQLite's strftime — keeps the comparison correct regardless of the
    SQLite runtime locale and avoids type coercion surprises.
  - On write: datetime → int(dt.timestamp())
  - On read:  int → datetime.fromtimestamp(v, tz=timezone.utc)
  - InvitationRow (from storage.invitation_repo) is reused; it always
    carries timezone-aware datetime objects at the Python boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.invitation_repo import InvitationRow

_DDL = """
CREATE TABLE IF NOT EXISTS invitations (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    email        TEXT NOT NULL,
    role         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'pending',
    invited_by   TEXT NOT NULL,
    expires_at   INTEGER NOT NULL,
    created_at   INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
)
"""

_SELECT_COLS = (
    "id, org_id, email, role, token_hash, status, invited_by, expires_at, created_at"
)


def _to_epoch(dt: datetime) -> int:
    """Convert a timezone-aware datetime to integer epoch seconds."""
    return int(dt.timestamp())


def _from_epoch(v: Any) -> datetime:
    """Convert an integer epoch to a UTC-aware datetime."""
    return datetime.fromtimestamp(int(v), tz=timezone.utc)


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _row_to_invitation(row: Any) -> InvitationRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return InvitationRow(
        id=m["id"],
        org_id=m["org_id"],
        email=m["email"],
        role=m["role"],
        token_hash=m["token_hash"],
        status=m["status"],
        invited_by=m["invited_by"],
        expires_at=_from_epoch(m["expires_at"]),
        created_at=_from_epoch(m["created_at"]),
    )


class SqliteInvitationRepository:
    """Concrete store for `invitations` over a SQLite AsyncEngine (lite mode)."""

    name: str = "sqlite_invitation_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL))

    async def _get_by_id(self, id: str) -> InvitationRow | None:
        """Fetch an invitation row by PK regardless of status or expiry."""
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_SELECT_COLS} FROM invitations WHERE id = :id"),
                    {"id": id},
                )
            ).first()
        return _row_to_invitation(row) if row else None

    async def create(
        self,
        *,
        id: str,
        org_id: str,
        email: str,
        role: str,
        token_hash: str,
        invited_by: str,
        expires_at: datetime,
    ) -> InvitationRow:
        exp_epoch = _to_epoch(expires_at)
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO invitations"
                    " (id, org_id, email, role, token_hash, invited_by, expires_at)"
                    " VALUES (:id, :org_id, :email, :role, :token_hash, :invited_by, :expires_at)"
                ),
                {
                    "id": id,
                    "org_id": org_id,
                    "email": email,
                    "role": role,
                    "token_hash": token_hash,
                    "invited_by": invited_by,
                    "expires_at": exp_epoch,
                },
            )
        got = await self._get_by_id(id)
        if got is None:
            raise RuntimeError(f"invitation {id!r} vanished after insert")
        return got

    async def get_pending_by_hash(self, token_hash: str) -> InvitationRow | None:
        now = _now_epoch()
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        f"SELECT {_SELECT_COLS} FROM invitations"
                        " WHERE token_hash = :h"
                        "   AND status = 'pending'"
                        "   AND expires_at > :now"
                    ),
                    {"h": token_hash, "now": now},
                )
            ).first()
        return _row_to_invitation(row) if row else None

    async def list_for_org(self, org_id: str) -> list[InvitationRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        f"SELECT {_SELECT_COLS} FROM invitations"
                        " WHERE org_id = :org_id"
                        " ORDER BY created_at DESC"
                    ),
                    {"org_id": org_id},
                )
            ).fetchall()
        return [_row_to_invitation(r) for r in rows]

    async def mark_accepted(self, id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("UPDATE invitations SET status = 'accepted' WHERE id = :id"),
                {"id": id},
            )

    async def revoke(self, id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text("UPDATE invitations SET status = 'revoked' WHERE id = :id"),
                {"id": id},
            )


__all__ = ["SqliteInvitationRepository"]
