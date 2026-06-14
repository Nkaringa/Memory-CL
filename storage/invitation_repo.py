"""Invitation store — token-hash-keyed org membership invitations.

Only the SHA-256 hash of the raw invite token is persisted here.  The caller
is responsible for hashing with SHA-256 before passing `token_hash`.

Columns:
  id           — stable opaque PK (caller-generated)
  org_id       — FK → orgs(org_id)
  email        — invitee e-mail address
  role         — role to grant upon acceptance
  token_hash   — SHA-256 hex of the raw invite token (UNIQUE)
  status       — 'pending' | 'accepted' | 'revoked'  (DEFAULT 'pending')
  invited_by   — user_id of the inviter
  expires_at   — TIMESTAMPTZ (caller-supplied, timezone-aware)
  created_at   — set by DB DEFAULT now()

B14/B15 reminder: TIMESTAMPTZ binds inside CTE-shaped statements need an
explicit CAST. The INSERT here is a plain INSERT, so asyncpg can infer the
type from the column definition — no explicit CAST required.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.invitation_repo")

_DDL = """
CREATE TABLE IF NOT EXISTS invitations (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL,
    email        TEXT NOT NULL,
    role         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'pending',
    invited_by   TEXT NOT NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_SELECT_COLS = (
    "id, org_id, email, role, token_hash, status, invited_by, expires_at, created_at"
)


@dataclass(frozen=True, slots=True)
class InvitationRow:
    id: str
    org_id: str
    email: str
    role: str
    token_hash: str
    status: str
    invited_by: str
    expires_at: datetime
    created_at: datetime


def _row_to_invitation(row: object) -> InvitationRow:
    m = row._mapping if hasattr(row, "_mapping") else row  # type: ignore[union-attr]
    return InvitationRow(
        id=m["id"],
        org_id=m["org_id"],
        email=m["email"],
        role=m["role"],
        token_hash=m["token_hash"],
        status=m["status"],
        invited_by=m["invited_by"],
        expires_at=m["expires_at"],
        created_at=m["created_at"],
    )


class PostgresInvitationRepository:
    """Concrete store for `invitations` over a Postgres AsyncEngine."""

    name: str = "invitation_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("invitation_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                await conn.execute(text(_DDL))

    async def _get_by_id(self, id: str) -> InvitationRow | None:
        """Fetch an invitation row by PK regardless of status or expiry."""
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        f"SELECT {_SELECT_COLS} FROM invitations WHERE id = :id"
                    ),
                    {"id": id},
                )
            ).mappings().first()
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
        with _tracer.start_as_current_span("invitation_repo.create"):
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
                        "expires_at": expires_at,
                    },
                )
        got = await self._get_by_id(id)
        if got is None:
            raise RuntimeError(f"invitation {id!r} vanished after insert")
        return got

    async def get_pending_by_hash(self, token_hash: str) -> InvitationRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        f"SELECT {_SELECT_COLS} FROM invitations"
                        " WHERE token_hash = :h"
                        "   AND status = 'pending'"
                        "   AND expires_at > now()"
                    ),
                    {"h": token_hash},
                )
            ).mappings().first()
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
            ).mappings().fetchall()
        return [_row_to_invitation(r) for r in rows]

    async def mark_accepted(self, id: str) -> None:
        with _tracer.start_as_current_span("invitation_repo.mark_accepted"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("UPDATE invitations SET status = 'accepted' WHERE id = :id"),
                    {"id": id},
                )

    async def revoke(self, id: str) -> None:
        with _tracer.start_as_current_span("invitation_repo.revoke"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text("UPDATE invitations SET status = 'revoked' WHERE id = :id"),
                    {"id": id},
                )


__all__ = ["InvitationRow", "PostgresInvitationRepository"]
