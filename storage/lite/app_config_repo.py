"""SQLite-backed app_config (single row) for lite mode.

Same surface + AppConfigRow as the Postgres `AppConfigRepository`, so
RuntimeConfig and the config router can't tell the difference. SQLite
flavor: BOOLEAN -> INTEGER 0/1, TIMESTAMPTZ -> ISO TEXT, no CTE casts, the
webhook_secret column is just part of CREATE TABLE (no ALTER needed).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.app_config_repo import AppConfigRow

_CONFIG_ID = 1

_DDL = """
CREATE TABLE IF NOT EXISTS app_config (
    id                   INTEGER PRIMARY KEY,
    mcp_api_key          TEXT,
    openai_api_key       TEXT,
    embedding_mode       TEXT NOT NULL DEFAULT 'openai',
    embedding_model      TEXT,
    onboarding_completed INTEGER NOT NULL DEFAULT 0,
    webhook_secret       TEXT,
    updated_at           TEXT
)
"""

_SELECT = text("SELECT * FROM app_config WHERE id = :id")

_UPSERT = text("""
INSERT INTO app_config (
    id, mcp_api_key, openai_api_key, embedding_mode,
    embedding_model, onboarding_completed, webhook_secret, updated_at
) VALUES (
    :id, :mcp_api_key, :openai_api_key, :embedding_mode,
    :embedding_model, :onboarding_completed, :webhook_secret, :updated_at
)
ON CONFLICT(id) DO UPDATE SET
    mcp_api_key          = excluded.mcp_api_key,
    openai_api_key       = excluded.openai_api_key,
    embedding_mode       = excluded.embedding_mode,
    embedding_model      = excluded.embedding_model,
    onboarding_completed = excluded.onboarding_completed,
    webhook_secret       = excluded.webhook_secret,
    updated_at           = excluded.updated_at
""")


def _parse_dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _row_to_config(row: Any) -> AppConfigRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return AppConfigRow(
        id=int(m["id"]),
        mcp_api_key=m["mcp_api_key"],
        openai_api_key=m["openai_api_key"],
        embedding_mode=m["embedding_mode"],
        embedding_model=m["embedding_model"],
        onboarding_completed=bool(m["onboarding_completed"]),
        webhook_secret=m["webhook_secret"],
        updated_at=_parse_dt(m["updated_at"]),
    )


class SqliteAppConfigRepository:
    name: str = "sqlite_app_config_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL))

    async def get(self) -> AppConfigRow | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(_SELECT, {"id": _CONFIG_ID})).first()
            return _row_to_config(row) if row else None

    async def upsert(self, **fields: Any) -> AppConfigRow:
        current = await self.get()
        merged: dict[str, Any] = {
            "mcp_api_key": current.mcp_api_key if current else None,
            "openai_api_key": current.openai_api_key if current else None,
            "embedding_mode": current.embedding_mode if current else "openai",
            "embedding_model": current.embedding_model if current else None,
            "onboarding_completed": current.onboarding_completed if current else False,
            "webhook_secret": current.webhook_secret if current else None,
        }
        for key in merged:
            if key in fields:
                merged[key] = fields[key]
        now = datetime.now(UTC)
        async with self._engine.begin() as conn:
            await conn.execute(_UPSERT, {
                "id": _CONFIG_ID,
                "mcp_api_key": merged["mcp_api_key"],
                "openai_api_key": merged["openai_api_key"],
                "embedding_mode": merged["embedding_mode"],
                "embedding_model": merged["embedding_model"],
                "onboarding_completed": int(bool(merged["onboarding_completed"])),
                "webhook_secret": merged["webhook_secret"],
                "updated_at": now.isoformat(),
            })
        return AppConfigRow(
            id=_CONFIG_ID,
            mcp_api_key=merged["mcp_api_key"],
            openai_api_key=merged["openai_api_key"],
            embedding_mode=merged["embedding_mode"],
            embedding_model=merged["embedding_model"],
            onboarding_completed=bool(merged["onboarding_completed"]),
            webhook_secret=merged["webhook_secret"],
            updated_at=now,
        )

    async def set_mcp_api_key(self, key: str | None) -> AppConfigRow:
        return await self.upsert(mcp_api_key=key)

    async def set_openai_api_key(self, key: str | None) -> AppConfigRow:
        return await self.upsert(openai_api_key=key)

    async def set_embedding_mode(self, mode: str) -> AppConfigRow:
        return await self.upsert(embedding_mode=mode)

    async def set_embedding_model(self, model: str | None) -> AppConfigRow:
        return await self.upsert(embedding_model=model)

    async def set_webhook_secret(self, secret: str | None) -> AppConfigRow:
        return await self.upsert(webhook_secret=secret)

    async def set_onboarding_completed(self, completed: bool) -> AppConfigRow:
        return await self.upsert(onboarding_completed=completed)


__all__ = ["SqliteAppConfigRepository"]
