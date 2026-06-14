"""SQLite-backed user + local credentials repo for lite mode.

Same surface as PostgresUserRepository.  SQLite differences:
  - created_at / updated_at use TEXT + DEFAULT (datetime('now')) — no TIMESTAMPTZ
  - ISO TEXT timestamps are hydrated to datetime via _parse_dt (same helper
    pattern as storage/lite/app_config_repo.py and storage/lite/org_repo.py)
  - UNIQUE(email) raises IntegrityError on duplicates — same semantics as PG
  - ON CONFLICT(user_id) DO UPDATE works identically in SQLite
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.user_repo import UserRow

_DDL_USERS = """
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    email        TEXT NOT NULL,
    display_name TEXT NOT NULL,
    avatar_url   TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (email)
)
"""

_DDL_LOCAL_CREDS = """
CREATE TABLE IF NOT EXISTS local_credentials (
    user_id       TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_DDL_STATEMENTS: tuple[str, ...] = (_DDL_USERS, _DDL_LOCAL_CREDS)


def _parse_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _row_to_user(row: Any) -> UserRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return UserRow(
        user_id=m["user_id"],
        email=m["email"],
        display_name=m["display_name"],
        avatar_url=m["avatar_url"],
        status=m["status"],
        created_at=_parse_dt(m["created_at"]),
    )


class SqliteUserRepository:
    """Concrete store for `users` + `local_credentials` over a SQLite AsyncEngine."""

    name: str = "sqlite_user_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            for stmt in _DDL_STATEMENTS:
                await conn.execute(text(stmt))

    async def create_user(
        self,
        *,
        user_id: str,
        email: str,
        display_name: str,
        avatar_url: str = "",
    ) -> UserRow:
        clean_email = email.strip().lower()
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO users (user_id, email, display_name, avatar_url)"
                    " VALUES (:uid, :e, :dn, :au)"
                ),
                {"uid": user_id, "e": clean_email, "dn": display_name, "au": avatar_url},
            )
        got = await self.get_user(user_id)
        if got is None:
            raise RuntimeError(f"user {user_id!r} vanished after insert")
        return got

    async def get_user(self, user_id: str) -> UserRow | None:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT user_id, email, display_name, avatar_url, status, created_at"
                        " FROM users WHERE user_id = :uid"
                    ),
                    {"uid": user_id},
                )
            ).first()
        return _row_to_user(row) if row else None

    async def get_by_email(self, email: str) -> UserRow | None:
        clean_email = email.strip().lower()
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT user_id, email, display_name, avatar_url, status, created_at"
                        " FROM users WHERE email = :e"
                    ),
                    {"e": clean_email},
                )
            ).first()
        return _row_to_user(row) if row else None

    async def count_users(self) -> int:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM users"))
            return int(result.scalar() or 0)

    async def set_password(self, *, user_id: str, password_hash: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO local_credentials (user_id, password_hash)"
                    " VALUES (:uid, :h)"
                    " ON CONFLICT(user_id) DO UPDATE"
                    " SET password_hash = excluded.password_hash,"
                    "     updated_at = datetime('now')"
                ),
                {"uid": user_id, "h": password_hash},
            )

    async def get_password_hash(self, user_id: str) -> str | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT password_hash FROM local_credentials WHERE user_id = :uid"),
                {"uid": user_id},
            )
            row = result.first()
        return str(row[0]) if row else None


__all__ = ["SqliteUserRepository"]
