"""Durable session store — server-side sessions keyed by a SHA-256 cookie hash.

The raw cookie value is NEVER stored here. The caller hashes it with SHA-256
before passing it as `session_id`. This table holds the server-side state for
each live session; the client carries only the opaque cookie value.

Columns:
  session_id   — SHA-256 hex of the raw cookie (PK)
  user_id      — FK → users(user_id)
  active_org_id — the organisation context for this session
  csrf_token   — per-session CSRF double-submit value
  created_at   — set by DB DEFAULT now()
  expires_at   — caller-supplied TIMESTAMPTZ (pass a timezone-aware datetime)
  revoked_at   — NULL until the session is explicitly revoked

B14/B15 reminder: TIMESTAMPTZ binds inside CTE-shaped statements need an
explicit CAST. The INSERT here is a plain INSERT (not a CTE), so asyncpg can
infer the type from the column definition — no explicit CAST is required for
the expires_at parameter. The created_at column uses DEFAULT now() and is
never written by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.session_repo")

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    active_org_id TEXT NOT NULL,
    csrf_token    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    revoked_at    TIMESTAMPTZ
)
"""


@dataclass(frozen=True, slots=True)
class SessionRow:
    session_id: str
    user_id: str
    active_org_id: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


def _row_to_session(row: object) -> SessionRow:
    m = row._mapping if hasattr(row, "_mapping") else row  # type: ignore[union-attr]
    return SessionRow(
        session_id=m["session_id"],
        user_id=m["user_id"],
        active_org_id=m["active_org_id"],
        csrf_token=m["csrf_token"],
        created_at=m["created_at"],
        expires_at=m["expires_at"],
        revoked_at=m["revoked_at"],
    )


class PostgresSessionRepository:
    """Concrete store for `sessions` over a Postgres AsyncEngine."""

    name: str = "session_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("session_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                await conn.execute(text(_DDL))

    async def _get_any(self, session_id: str) -> SessionRow | None:
        """Fetch a session row regardless of expiry or revocation status."""
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT session_id, user_id, active_org_id, csrf_token,"
                        " created_at, expires_at, revoked_at"
                        " FROM sessions WHERE session_id = :sid"
                    ),
                    {"sid": session_id},
                )
            ).mappings().first()
        return _row_to_session(row) if row else None

    async def create_session(
        self,
        *,
        session_id: str,
        user_id: str,
        active_org_id: str,
        csrf_token: str,
        expires_at: datetime,
    ) -> SessionRow:
        with _tracer.start_as_current_span("session_repo.create_session"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO sessions"
                        " (session_id, user_id, active_org_id, csrf_token, expires_at)"
                        " VALUES (:sid, :uid, :oid, :csrf, :exp)"
                    ),
                    {
                        "sid": session_id,
                        "uid": user_id,
                        "oid": active_org_id,
                        "csrf": csrf_token,
                        "exp": expires_at,
                    },
                )
        got = await self._get_any(session_id)
        if got is None:
            raise RuntimeError(f"session {session_id!r} vanished after insert")
        return got

    async def get_active(self, session_id: str) -> SessionRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT session_id, user_id, active_org_id, csrf_token,"
                        " created_at, expires_at, revoked_at"
                        " FROM sessions"
                        " WHERE session_id = :sid"
                        "   AND revoked_at IS NULL"
                        "   AND expires_at > now()"
                    ),
                    {"sid": session_id},
                )
            ).mappings().first()
        return _row_to_session(row) if row else None

    async def revoke(self, session_id: str) -> None:
        with _tracer.start_as_current_span("session_repo.revoke"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE sessions SET revoked_at = now()"
                        " WHERE session_id = :sid"
                    ),
                    {"sid": session_id},
                )

    async def list_active_session_ids(self) -> set[str]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT session_id FROM sessions"
                        " WHERE revoked_at IS NULL AND expires_at > now()"
                    )
                )
            ).fetchall()
        return {r[0] for r in rows}


__all__ = ["PostgresSessionRepository", "SessionRow"]
