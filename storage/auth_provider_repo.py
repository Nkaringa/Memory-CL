"""Named OIDC/OAuth2 provider configuration.

Each row represents one configured identity provider (Google, GitHub, GitLab,
etc.). Operators can add, update, enable/disable, and remove providers without
restarting the server.  Only enabled providers are surfaced to end-users at
the login page.

SQL lives only here; `ensure_schema()` is idempotent and runs in the same
bootstrap pass as the other tables. B14/B15: TIMESTAMPTZ binds are CAST.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.auth_provider_repo")

_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS auth_providers (
        id             TEXT PRIMARY KEY,
        provider_type  TEXT NOT NULL,
        display_name   TEXT NOT NULL,
        client_id      TEXT NOT NULL,
        client_secret  TEXT NOT NULL,
        discovery_url  TEXT,
        scopes         TEXT,
        enabled        BOOLEAN NOT NULL DEFAULT false,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ
    )
    """,
)


@dataclass(frozen=True, slots=True)
class AuthProviderRow:
    id: str
    provider_type: str
    display_name: str
    client_id: str
    client_secret: str
    discovery_url: str | None
    scopes: str | None
    enabled: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


_SELECT_COLS = (
    "id, provider_type, display_name, client_id, client_secret, "
    "discovery_url, scopes, enabled, created_at, updated_at"
)

_INSERT = text(f"""
INSERT INTO auth_providers
    (id, provider_type, display_name, client_id, client_secret,
     discovery_url, scopes, enabled)
VALUES
    (:id, :provider_type, :display_name, :client_id, :client_secret,
     :discovery_url, :scopes, :enabled)
""")

_SELECT_ONE = text(f"SELECT {_SELECT_COLS} FROM auth_providers WHERE id = :id")

_SELECT_ALL = text(
    f"SELECT {_SELECT_COLS} FROM auth_providers ORDER BY created_at"
)

_SELECT_ENABLED = text(
    f"SELECT {_SELECT_COLS} FROM auth_providers WHERE enabled = true ORDER BY created_at"
)

_UPDATE = text("""
UPDATE auth_providers
SET display_name = :display_name,
    client_id    = :client_id,
    client_secret = :client_secret,
    discovery_url = :discovery_url,
    scopes        = :scopes,
    updated_at    = CAST(:ts AS TIMESTAMPTZ)
WHERE id = :id
""")

_SET_ENABLED = text("""
UPDATE auth_providers
SET enabled    = :enabled,
    updated_at = CAST(:ts AS TIMESTAMPTZ)
WHERE id = :id
""")

_DELETE = text("DELETE FROM auth_providers WHERE id = :id")


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
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


class PostgresAuthProviderRepository:
    """Concrete store for `auth_providers` over the injected AsyncEngine."""

    name: str = "auth_provider_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("auth_provider_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                for stmt in _DDL_STATEMENTS:
                    await conn.execute(text(stmt))

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
        with _tracer.start_as_current_span("auth_provider_repo.create"):
            async with self._engine.begin() as conn:
                await conn.execute(_INSERT, {
                    "id": id,
                    "provider_type": provider_type,
                    "display_name": display_name,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "discovery_url": discovery_url,
                    "scopes": scopes,
                    "enabled": enabled,
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
        with _tracer.start_as_current_span("auth_provider_repo.update"):
            async with self._engine.begin() as conn:
                await conn.execute(_UPDATE, {
                    "id": id,
                    "display_name": display_name,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "discovery_url": discovery_url,
                    "scopes": scopes,
                    "ts": datetime.now(UTC),
                })
        got = await self.get(id)
        if got is None:
            raise RuntimeError(f"auth_provider {id!r} not found after update")
        return got

    async def set_enabled(self, *, id: str, enabled: bool) -> None:
        with _tracer.start_as_current_span("auth_provider_repo.set_enabled"):
            async with self._engine.begin() as conn:
                await conn.execute(_SET_ENABLED, {
                    "id": id,
                    "enabled": enabled,
                    "ts": datetime.now(UTC),
                })

    async def delete(self, id: str) -> None:
        with _tracer.start_as_current_span("auth_provider_repo.delete"):
            async with self._engine.begin() as conn:
                await conn.execute(_DELETE, {"id": id})


__all__ = ["AuthProviderRow", "PostgresAuthProviderRepository"]
