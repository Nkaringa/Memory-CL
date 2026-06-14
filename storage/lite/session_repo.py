"""SQLite-backed session repo for lite mode.

Same surface as PostgresSessionRepository. SQLite deviations:
  - Timestamp columns are stored as INTEGER epoch seconds (not TIMESTAMPTZ):
      created_at  INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
      expires_at  INTEGER NOT NULL
      revoked_at  INTEGER
  - Expiry comparison uses a Python-computed epoch bound (:now) rather than
    SQLite's strftime — this keeps the comparison correct regardless of the
    SQLite runtime locale and avoids type coercion surprises.
  - On write: datetime → int(dt.timestamp())
  - On read:  int → datetime.fromtimestamp(v, tz=timezone.utc)
  - SessionRow (from storage.session_repo) is reused; it always carries
    timezone-aware datetime objects at the Python boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.session_repo import SessionRow

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    active_org_id TEXT NOT NULL,
    csrf_token    TEXT NOT NULL,
    created_at    INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    expires_at    INTEGER NOT NULL,
    revoked_at    INTEGER
)
"""


def _to_epoch(dt: datetime) -> int:
    """Convert a timezone-aware datetime to integer epoch seconds."""
    return int(dt.timestamp())


def _from_epoch(v: Any) -> datetime | None:
    """Convert an integer epoch (or None) to a UTC-aware datetime."""
    if v is None:
        return None
    return datetime.fromtimestamp(int(v), tz=timezone.utc)


def _row_to_session(row: Any) -> SessionRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    created = _from_epoch(m["created_at"])
    expires = _from_epoch(m["expires_at"])
    revoked = _from_epoch(m["revoked_at"])
    if created is None or expires is None:
        raise RuntimeError("sessions row has NULL created_at or expires_at — schema violation")
    return SessionRow(
        session_id=m["session_id"],
        user_id=m["user_id"],
        active_org_id=m["active_org_id"],
        csrf_token=m["csrf_token"],
        created_at=created,
        expires_at=expires,
        revoked_at=revoked,
    )


def _now_epoch() -> int:
    return int(datetime.now(timezone.utc).timestamp())


class SqliteSessionRepository:
    """Concrete store for `sessions` over a SQLite AsyncEngine (lite mode)."""

    name: str = "sqlite_session_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
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
            ).first()
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
        exp_epoch = _to_epoch(expires_at)
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
                    "exp": exp_epoch,
                },
            )
        got = await self._get_any(session_id)
        if got is None:
            raise RuntimeError(f"session {session_id!r} vanished after insert")
        return got

    async def get_active(self, session_id: str) -> SessionRow | None:
        now = _now_epoch()
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT session_id, user_id, active_org_id, csrf_token,"
                        " created_at, expires_at, revoked_at"
                        " FROM sessions"
                        " WHERE session_id = :sid"
                        "   AND revoked_at IS NULL"
                        "   AND expires_at > :now"
                    ),
                    {"sid": session_id, "now": now},
                )
            ).first()
        return _row_to_session(row) if row else None

    async def revoke(self, session_id: str) -> None:
        now = _now_epoch()
        async with self._engine.begin() as conn:
            await conn.execute(
                text("UPDATE sessions SET revoked_at = :now WHERE session_id = :sid"),
                {"now": now, "sid": session_id},
            )

    async def list_active_session_ids(self) -> set[str]:
        now = _now_epoch()
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT session_id FROM sessions"
                        " WHERE revoked_at IS NULL AND expires_at > :now"
                    ),
                    {"now": now},
                )
            ).fetchall()
        return {r[0] for r in rows}


__all__ = ["SqliteSessionRepository"]
