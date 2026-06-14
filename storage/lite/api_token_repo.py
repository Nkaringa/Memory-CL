"""SQLite-backed api_tokens for lite mode.

Same surface + ApiTokenRow + hash_token as the Postgres version. SQLite
flavor: TIMESTAMPTZ -> ISO TEXT. Stores only the token hash.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.api_token_repo import ApiTokenRow, hash_token

_TOKEN_ENTROPY_BYTES = 32
_ID_ENTROPY_BYTES = 8

_DDL = """
CREATE TABLE IF NOT EXISTS api_tokens (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    token_hint   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
)
"""

_INSERT = text(
    "INSERT INTO api_tokens (id, name, token_hash, token_hint, created_at) "
    "VALUES (:id, :name, :token_hash, :token_hint, :ts)"
)
_SELECT_ALL = text(
    "SELECT id, name, token_hint, created_at, last_used_at, revoked_at "
    "FROM api_tokens ORDER BY created_at DESC"
)
_SELECT_ONE = text(
    "SELECT id, name, token_hint, created_at, last_used_at, revoked_at "
    "FROM api_tokens WHERE id = :id"
)
_SELECT_ACTIVE = text("SELECT token_hash FROM api_tokens WHERE revoked_at IS NULL")
_REVOKE = text(
    "UPDATE api_tokens SET revoked_at = :ts WHERE id = :id AND revoked_at IS NULL"
)
_TOUCH = text("UPDATE api_tokens SET last_used_at = :ts WHERE token_hash = :token_hash")


def _dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _hint(raw: str) -> str:
    return "••••" + raw[-4:] if len(raw) > 4 else "••••"


def _row(r: Any) -> ApiTokenRow:
    m = r._mapping if hasattr(r, "_mapping") else r
    return ApiTokenRow(
        id=m["id"], name=m["name"], token_hint=m["token_hint"],
        created_at=_dt(m["created_at"]), last_used_at=_dt(m["last_used_at"]),
        revoked_at=_dt(m["revoked_at"]),
    )


class SqliteApiTokenRepository:
    name: str = "sqlite_api_token_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL))

    async def issue(self, name: str) -> tuple[str, ApiTokenRow]:
        raw = secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)
        token_id = secrets.token_urlsafe(_ID_ENTROPY_BYTES)
        clean_name = name.strip() or "unnamed"
        hint = _hint(raw)
        now = datetime.now(UTC)
        async with self._engine.begin() as conn:
            await conn.execute(_INSERT, {
                "id": token_id, "name": clean_name,
                "token_hash": hash_token(raw), "token_hint": hint,
                "ts": now.isoformat(),
            })
        return raw, ApiTokenRow(
            id=token_id, name=clean_name, token_hint=hint,
            created_at=now, last_used_at=None, revoked_at=None,
        )

    async def list_all(self) -> list[ApiTokenRow]:
        async with self._engine.connect() as conn:
            return [_row(r) for r in (await conn.execute(_SELECT_ALL)).fetchall()]

    async def get(self, token_id: str) -> ApiTokenRow | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(_SELECT_ONE, {"id": token_id})).first()
            return _row(row) if row else None

    async def list_active_hashes(self) -> set[str]:
        async with self._engine.connect() as conn:
            return {r[0] for r in (await conn.execute(_SELECT_ACTIVE)).fetchall()}

    async def revoke(self, token_id: str) -> bool:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                _REVOKE, {"id": token_id, "ts": datetime.now(UTC).isoformat()}
            )
            return (result.rowcount or 0) > 0

    async def touch_last_used(self, token_hash: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                _TOUCH, {"token_hash": token_hash, "ts": datetime.now(UTC).isoformat()}
            )


__all__ = ["SqliteApiTokenRepository"]
