"""SQLite-backed federated_identities for lite mode.

Same surface + FederatedIdentityRow as the Postgres version.  SQLite differences:
  - TIMESTAMPTZ -> TEXT + DEFAULT (datetime('now'))
  - _parse_dt hydrates ISO TEXT timestamps to datetime objects
  - UNIQUE(provider, subject) in DDL raises IntegrityError on duplicates — same
    semantics as PG
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.federated_identity_repo import FederatedIdentityRow

_DDL = """
CREATE TABLE IF NOT EXISTS federated_identities (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    provider   TEXT NOT NULL,
    subject    TEXT NOT NULL,
    email      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (provider, subject)
)
"""

_SELECT_COLS = "id, user_id, provider, subject, email, created_at"

_INSERT = text(
    "INSERT INTO federated_identities (id, user_id, provider, subject, email)"
    " VALUES (:id, :user_id, :provider, :subject, :email)"
)

_SELECT_BY_SUBJECT = text(
    f"SELECT {_SELECT_COLS} FROM federated_identities"
    " WHERE provider = :provider AND subject = :subject"
)

_SELECT_FOR_USER = text(
    f"SELECT {_SELECT_COLS} FROM federated_identities"
    " WHERE user_id = :user_id ORDER BY created_at"
)


def _parse_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _row(r: Any) -> FederatedIdentityRow:
    m = r._mapping if hasattr(r, "_mapping") else r
    return FederatedIdentityRow(
        id=m["id"],
        user_id=m["user_id"],
        provider=m["provider"],
        subject=m["subject"],
        email=m["email"],
        created_at=_parse_dt(m["created_at"]),
    )


class SqliteFederatedIdentityRepository:
    """Concrete store for `federated_identities` over a SQLite AsyncEngine."""

    name: str = "sqlite_federated_identity_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL))

    async def add(
        self,
        *,
        id: str,
        user_id: str,
        provider: str,
        subject: str,
        email: str,
    ) -> FederatedIdentityRow:
        async with self._engine.begin() as conn:
            await conn.execute(_INSERT, {
                "id": id,
                "user_id": user_id,
                "provider": provider,
                "subject": subject,
                "email": email,
            })
        got = await self.get_by_subject(provider=provider, subject=subject)
        if got is None:
            raise RuntimeError(f"federated_identity ({provider!r}, {subject!r}) vanished after insert")
        return got

    async def get_by_subject(self, *, provider: str, subject: str) -> FederatedIdentityRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(_SELECT_BY_SUBJECT, {"provider": provider, "subject": subject})
            ).first()
        return _row(row) if row else None

    async def list_for_user(self, user_id: str) -> list[FederatedIdentityRow]:
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(_SELECT_FOR_USER, {"user_id": user_id})
            ).fetchall()
        return [_row(r) for r in rows]


__all__ = ["SqliteFederatedIdentityRepository"]
