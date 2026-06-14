"""Durable user store — human identity rows and local (argon2id) credentials.

Two tables managed here:
  - `users`: one row per registered human; email is stored lowercased and has
    a UNIQUE constraint so duplicate accounts are rejected at the DB level.
  - `local_credentials`: one row per user who authenticates with a password;
    linked to `users` via FK.  Federated (OAuth/OIDC) identities are a later
    phase and live in a separate table.

B14/B15 reminder: TIMESTAMPTZ binds inside CTE-shaped statements need an
explicit CAST.  The INSERT here is plain (no CTE), so no cast is needed for
the default `now()` column.  `set_password` uses a plain UPDATE/upsert with
a `now()` call — also no cast needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.user_repo")

_DDL_USERS = """
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    email        TEXT NOT NULL,
    display_name TEXT NOT NULL,
    avatar_url   TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (email)
)
"""

_DDL_LOCAL_CREDS = """
CREATE TABLE IF NOT EXISTS local_credentials (
    user_id       TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_DDL_STATEMENTS: tuple[str, ...] = (_DDL_USERS, _DDL_LOCAL_CREDS)


@dataclass(frozen=True, slots=True)
class UserRow:
    user_id: str
    email: str
    display_name: str
    avatar_url: str = ""
    status: str = "active"
    created_at: datetime | None = None


def _row_to_user(row: object) -> UserRow:
    m = row._mapping if hasattr(row, "_mapping") else row  # type: ignore[union-attr]
    return UserRow(
        user_id=m["user_id"],
        email=m["email"],
        display_name=m["display_name"],
        avatar_url=m["avatar_url"],
        status=m["status"],
        created_at=m["created_at"],
    )


class PostgresUserRepository:
    """Concrete store for `users` + `local_credentials` over a Postgres AsyncEngine.

    Mirrors `PostgresOrgRepository`: SQL lives only here, the engine is
    injected, and `ensure_schema()` is idempotent.
    """

    name: str = "user_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("user_repo.ensure_schema"):
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
        with _tracer.start_as_current_span("user_repo.create_user"):
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
            ).mappings().first()
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
            ).mappings().first()
        return _row_to_user(row) if row else None

    async def count_users(self) -> int:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT count(*) FROM users"))
            return int(result.scalar() or 0)

    async def set_password(self, *, user_id: str, password_hash: str) -> None:
        with _tracer.start_as_current_span("user_repo.set_password"):
            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO local_credentials (user_id, password_hash)"
                        " VALUES (:uid, :h)"
                        " ON CONFLICT (user_id) DO UPDATE"
                        " SET password_hash = :h, updated_at = now()"
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


__all__ = ["PostgresUserRepository", "UserRow"]
