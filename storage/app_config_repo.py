"""Durable runtime-config store — the single `app_config` row.

Phase-1 onboarding: the operator can generate/rotate the MCP key, set
the OpenAI key, and choose the embedding mode self-serve. Those values
live HERE (Postgres), overriding the env-based `Settings` when present
and falling back to env when absent (see `core.config_runtime`).

There is exactly one logical row, pinned at `id = 1`. The table is
created idempotently alongside `ingestion_units` so a fresh deploy and
an upgraded deploy both converge on the same schema.

B14/B15 reminder: every non-TEXT bind inside a CTE-shaped statement
needs an explicit CAST (asyncpg can't infer the column type through the
WITH wrapper). The upsert below CASTs the BOOLEAN and TIMESTAMPTZ binds
for exactly that reason. B16 reminder: no ":name"-style tokens may appear
in any comment inside a `text()` string or SQLAlchemy treats them as
required bind parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observability import get_tracer

_tracer = get_tracer("storage.app_config_repo")

# The single-row PK. One logical config row per deployment.
_CONFIG_ID = 1


# Idempotent DDL — runs in the same ensure-schema pass as ingestion_units.
_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS app_config (
        id                   INTEGER PRIMARY KEY,
        mcp_api_key          TEXT,
        openai_api_key       TEXT,
        embedding_mode       TEXT NOT NULL DEFAULT 'openai',
        embedding_model      TEXT,
        onboarding_completed BOOLEAN NOT NULL DEFAULT false,
        updated_at           TIMESTAMPTZ
    )
    """,
)


@dataclass(frozen=True, slots=True)
class AppConfigRow:
    """The runtime-config row as stored. Keys are the raw secrets — only
    `core.config_runtime.RuntimeConfig` is allowed to read them, and the
    config router NEVER returns them verbatim (it masks / one-time-reveals).
    """

    id: int
    mcp_api_key: str | None
    openai_api_key: str | None
    embedding_mode: str
    embedding_model: str | None
    onboarding_completed: bool
    updated_at: datetime | None


_SELECT = text("SELECT * FROM app_config WHERE id = :id")


# Full-row upsert. Every field is supplied by the caller (RuntimeConfig
# reads-modifies-writes the whole row), so there is no partial-column
# COALESCE dance here — simpler and race-free for a single logical row.
_UPSERT = text("""
WITH input AS (
    SELECT
        CAST(:id AS INTEGER) AS id,
        :mcp_api_key AS mcp_api_key,
        :openai_api_key AS openai_api_key,
        :embedding_mode AS embedding_mode,
        :embedding_model AS embedding_model,
        CAST(:onboarding_completed AS BOOLEAN) AS onboarding_completed,
        CAST(:updated_at AS TIMESTAMPTZ) AS updated_at
)
INSERT INTO app_config (
    id, mcp_api_key, openai_api_key, embedding_mode,
    embedding_model, onboarding_completed, updated_at
) SELECT
    id, mcp_api_key, openai_api_key, embedding_mode,
    embedding_model, onboarding_completed, updated_at
  FROM input
ON CONFLICT (id) DO UPDATE SET
    mcp_api_key          = EXCLUDED.mcp_api_key,
    openai_api_key       = EXCLUDED.openai_api_key,
    embedding_mode       = EXCLUDED.embedding_mode,
    embedding_model      = EXCLUDED.embedding_model,
    onboarding_completed = EXCLUDED.onboarding_completed,
    updated_at           = EXCLUDED.updated_at
""")


def _row_to_config(row: Any) -> AppConfigRow:
    m = row._mapping if hasattr(row, "_mapping") else row
    return AppConfigRow(
        id=int(m["id"]),
        mcp_api_key=m["mcp_api_key"],
        openai_api_key=m["openai_api_key"],
        embedding_mode=m["embedding_mode"],
        embedding_model=m["embedding_model"],
        onboarding_completed=bool(m["onboarding_completed"]),
        updated_at=m["updated_at"],
    )


class AppConfigRepository:
    """Concrete store for the single `app_config` row over PostgresClient.

    Mirrors `PostgresIngestionRepository`: SQL lives only here, the engine
    is injected (via the same lazy `engine_proxy` the lifespan uses), and
    `ensure_schema()` is idempotent.
    """

    name: str = "app_config_repo"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    # ----- Bootstrap -----
    async def ensure_schema(self) -> None:
        with _tracer.start_as_current_span("app_config_repo.ensure_schema"):
            async with self._engine.begin() as conn:
                for stmt in _DDL_STATEMENTS:
                    await conn.execute(text(stmt))

    # ----- Reads -----
    async def get(self) -> AppConfigRow | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(_SELECT, {"id": _CONFIG_ID})
            row = result.first()
            return _row_to_config(row) if row else None

    # ----- Writes -----
    async def upsert(self, **fields: Any) -> AppConfigRow:
        """Read-modify-write the single row with the supplied overrides.

        Any field not passed keeps its current value (or the column
        default for a first insert). `updated_at` is always stamped.
        Returns the persisted row.
        """
        current = await self.get()
        merged = {
            "mcp_api_key": current.mcp_api_key if current else None,
            "openai_api_key": current.openai_api_key if current else None,
            "embedding_mode": current.embedding_mode if current else "openai",
            "embedding_model": current.embedding_model if current else None,
            "onboarding_completed": (
                current.onboarding_completed if current else False
            ),
        }
        for key in (
            "mcp_api_key",
            "openai_api_key",
            "embedding_mode",
            "embedding_model",
            "onboarding_completed",
        ):
            if key in fields:
                merged[key] = fields[key]

        params = {
            "id": _CONFIG_ID,
            "updated_at": datetime.now(UTC),
            **merged,
        }
        with _tracer.start_as_current_span("app_config_repo.upsert"):
            async with self._engine.begin() as conn:
                await conn.execute(_UPSERT, params)
        return AppConfigRow(
            id=_CONFIG_ID,
            mcp_api_key=merged["mcp_api_key"],  # type: ignore[arg-type]
            openai_api_key=merged["openai_api_key"],  # type: ignore[arg-type]
            embedding_mode=merged["embedding_mode"],  # type: ignore[arg-type]
            embedding_model=merged["embedding_model"],  # type: ignore[arg-type]
            onboarding_completed=merged["onboarding_completed"],  # type: ignore[arg-type]
            updated_at=params["updated_at"],  # type: ignore[arg-type]
        )

    # ----- Convenience field setters -----
    async def set_mcp_api_key(self, key: str | None) -> AppConfigRow:
        return await self.upsert(mcp_api_key=key)

    async def set_openai_api_key(self, key: str | None) -> AppConfigRow:
        return await self.upsert(openai_api_key=key)

    async def set_embedding_mode(self, mode: str) -> AppConfigRow:
        return await self.upsert(embedding_mode=mode)

    async def set_embedding_model(self, model: str | None) -> AppConfigRow:
        return await self.upsert(embedding_model=model)

    async def set_onboarding_completed(self, completed: bool) -> AppConfigRow:
        return await self.upsert(onboarding_completed=completed)


__all__ = ["AppConfigRepository", "AppConfigRow"]
