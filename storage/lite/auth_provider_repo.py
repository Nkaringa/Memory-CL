"""SQLite-backed auth_providers for lite mode.

Same surface + AuthProviderRow as the Postgres version. SQLite differences:
  - TIMESTAMPTZ -> TEXT + DEFAULT (datetime('now'))
  - BOOLEAN -> INTEGER 0|1; converted back to bool at the boundary in _row()
  - _parse_dt hydrates ISO TEXT timestamps to datetime objects
  - No CAST(:ts AS TIMESTAMPTZ) — plain TEXT bind
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.auth_provider_repo import AuthProviderRow

_DDL = """
CREATE TABLE IF NOT EXISTS auth_providers (
    id             TEXT PRIMARY KEY,
    provider_type  TEXT NOT NULL,
    display_name   TEXT NOT NULL,
    client_id      TEXT NOT NULL,
    client_secret  TEXT NOT NULL,
    discovery_url  TEXT,
    scopes         TEXT,
    enabled        INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT
)
"""

_SELECT_COLS = (
    "id, provider_type, display_name, client_id, client_secret, "
    "discovery_url, scopes, enabled, created_at, updated_at"
)

_INSERT = text(
    f"INSERT INTO auth_providers "
    f"(id, provider_type, display_name, client_id, client_secret, "
    f"discovery_url, scopes, enabled) "
    f"VALUES "
    f"(:id, :provider_type, :display_name, :client_id, :client_secret, "
    f":discovery_url, :scopes, :enabled)"
)

_SELECT_ONE = text(f"SELECT {_SELECT_COLS} FROM auth_providers WHERE id = :id")

_SELECT_ALL = text(
    f"SELECT {_SELECT_COLS} FROM auth_providers ORDER BY created_at"
)

_SELECT_ENABLED = text(
    f"SELECT {_SELECT_COLS} FROM auth_providers WHERE enabled = 1 ORDER BY created_at"
)

_UPDATE = text("""
UPDATE auth_providers
SET display_name  = :display_name,
    client_id     = :client_id,
    client_secret = :client_secret,
    discovery_url = :discovery_url,
    scopes        = :scopes,
    updated_at    = :ts
WHERE id = :id
""")

_SET_ENABLED = text(
    "UPDATE auth_providers SET enabled = :enabled, updated_at = :ts WHERE id = :id"
)

_DELETE = text("DELETE FROM auth_providers WHERE id = :id")


def _parse_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _row(r: Any) -> AuthProviderRow:
    m = r._mapping if hasattr(r, "_mapping") else r
    return AuthProviderRow(
        id=m["id"],
        provider_type=m["provider_type"],
        display_name=m["display_name"],
        client_id=m["client_id"],
        client_secret=m["client_secret"],
        discovery_url=m["discovery_url"],
        scopes=m["scopes"],
        enabled=bool(m["enabled"]),
        created_at=_parse_dt(m["created_at"]),
        updated_at=_parse_dt(m["updated_at"]),
    )


class SqliteAuthProviderRepository:
    name: str = "sqlite_auth_provider_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL))

    async def create(
        self,
        *,
        id: str,
        provider_type: str,
        display_name: str,
        client_id: str,
        client_secret: str,
        discovery_url: str | None,
        scopes: str | None,
        enabled: bool,
    ) -> AuthProviderRow:
        async with self._engine.begin() as conn:
            await conn.execute(_INSERT, {
                "id": id,
                "provider_type": provider_type,
                "display_name": display_name,
                "client_id": client_id,
                "client_secret": client_secret,
                "discovery_url": discovery_url,
                "scopes": scopes,
                "enabled": int(enabled),
            })
        got = await self.get(id)
        if got is None:
            raise RuntimeError(f"auth_provider {id!r} vanished after insert")
        return got

    async def get(self, id: str) -> AuthProviderRow | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(_SELECT_ONE, {"id": id})).first()
            return _row(row) if row else None

    async def list_all(self) -> list[AuthProviderRow]:
        async with self._engine.connect() as conn:
            return [_row(r) for r in (await conn.execute(_SELECT_ALL)).fetchall()]

    async def list_enabled(self) -> list[AuthProviderRow]:
        async with self._engine.connect() as conn:
            return [_row(r) for r in (await conn.execute(_SELECT_ENABLED)).fetchall()]

    async def update(
        self,
        *,
        id: str,
        display_name: str,
        client_id: str,
        client_secret: str,
        discovery_url: str | None,
        scopes: str | None,
    ) -> AuthProviderRow:
        async with self._engine.begin() as conn:
            await conn.execute(_UPDATE, {
                "id": id,
                "display_name": display_name,
                "client_id": client_id,
                "client_secret": client_secret,
                "discovery_url": discovery_url,
                "scopes": scopes,
                "ts": datetime.now(UTC).isoformat(),
            })
        got = await self.get(id)
        if got is None:
            raise RuntimeError(f"auth_provider {id!r} not found after update")
        return got

    async def set_enabled(self, *, id: str, enabled: bool) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_SET_ENABLED, {
                "id": id,
                "enabled": int(enabled),
                "ts": datetime.now(UTC).isoformat(),
            })

    async def delete(self, id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(_DELETE, {"id": id})


__all__ = ["SqliteAuthProviderRepository"]
