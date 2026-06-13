"""Unit tests for the runtime-config precedence + seed layer.

The repo is faked with an in-memory stand-in (the SQL itself is covered
by the golden integration test); these tests pin the RESOLUTION rules —
Postgres-over-env, env-fallback, cache invalidation — and the
seed-from-env idempotency that guarantees no-lockout on first boot.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.api.lifespan import _seed_app_config_from_env
from core.config import Settings
from core.config_runtime import RuntimeConfig
from storage.app_config_repo import AppConfigRow


class _FakeAppConfigRepo:
    """In-memory AppConfigRepository stand-in with the same surface."""

    def __init__(self, row: AppConfigRow | None = None) -> None:
        self._row = row

    async def get(self) -> AppConfigRow | None:
        return self._row

    async def upsert(self, **fields: object) -> AppConfigRow:
        base = self._row
        merged = {
            "mcp_api_key": base.mcp_api_key if base else None,
            "openai_api_key": base.openai_api_key if base else None,
            "embedding_mode": base.embedding_mode if base else "openai",
            "embedding_model": base.embedding_model if base else None,
            "onboarding_completed": base.onboarding_completed if base else False,
        }
        merged.update({k: v for k, v in fields.items() if k in merged})
        self._row = AppConfigRow(
            id=1,
            updated_at=datetime.now(UTC),
            **merged,  # type: ignore[arg-type]
        )
        return self._row

    async def set_mcp_api_key(self, key: str | None) -> AppConfigRow:
        return await self.upsert(mcp_api_key=key)

    async def set_openai_api_key(self, key: str | None) -> AppConfigRow:
        return await self.upsert(openai_api_key=key)

    async def set_embedding_mode(self, mode: str) -> AppConfigRow:
        return await self.upsert(embedding_mode=mode)

    async def set_onboarding_completed(self, done: bool) -> AppConfigRow:
        return await self.upsert(onboarding_completed=done)


def _row(**kw: object) -> AppConfigRow:
    base = {
        "id": 1,
        "mcp_api_key": None,
        "openai_api_key": None,
        "embedding_mode": "openai",
        "embedding_model": None,
        "onboarding_completed": False,
        "updated_at": datetime.now(UTC),
    }
    base.update(kw)
    return AppConfigRow(**base)  # type: ignore[arg-type]


def _settings(**kw: object) -> Settings:
    return Settings(**kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Precedence: Postgres over env
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mcp_key_postgres_overrides_env() -> None:
    repo = _FakeAppConfigRepo(_row(mcp_api_key="pg-key"))
    rc = RuntimeConfig(repo, _settings(mcp_api_key="env-key"))  # type: ignore[arg-type]
    await rc.refresh()
    assert rc.mcp_api_key() == "pg-key"
    assert rc.configured() is True


@pytest.mark.asyncio
async def test_mcp_key_falls_back_to_env_when_pg_unset() -> None:
    repo = _FakeAppConfigRepo(_row(mcp_api_key=None))
    rc = RuntimeConfig(repo, _settings(mcp_api_key="env-key"))  # type: ignore[arg-type]
    await rc.refresh()
    assert rc.mcp_api_key() == "env-key"


@pytest.mark.asyncio
async def test_mcp_key_none_when_neither_set() -> None:
    repo = _FakeAppConfigRepo(None)
    rc = RuntimeConfig(repo, _settings())  # type: ignore[arg-type]
    await rc.refresh()
    assert rc.mcp_api_key() is None
    assert rc.configured() is False


@pytest.mark.asyncio
async def test_openai_key_precedence_and_embeddings_enabled() -> None:
    repo = _FakeAppConfigRepo(_row(openai_api_key="sk-pg"))
    rc = RuntimeConfig(repo, _settings(openai_api_key="sk-env"))  # type: ignore[arg-type]
    await rc.refresh()
    assert rc.openai_api_key() == "sk-pg"
    assert rc.embeddings_enabled() is True


@pytest.mark.asyncio
async def test_embeddings_disabled_when_no_key() -> None:
    rc = RuntimeConfig(_FakeAppConfigRepo(None), _settings())  # type: ignore[arg-type]
    await rc.refresh()
    assert rc.embeddings_enabled() is False


@pytest.mark.asyncio
async def test_local_mode_does_not_enable_embeddings_without_key() -> None:
    """Phase-1: 'local' is accepted but the local embedder is Phase 2, so
    it must NOT flip embeddings_enabled on by itself."""
    repo = _FakeAppConfigRepo(_row(embedding_mode="local"))
    rc = RuntimeConfig(repo, _settings())  # type: ignore[arg-type]
    await rc.refresh()
    assert rc.embedding_mode() == "local"
    assert rc.embeddings_enabled() is False


# ---------------------------------------------------------------------------
# Cache invalidation / refresh
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_refresh_picks_up_writes() -> None:
    repo = _FakeAppConfigRepo(None)
    rc = RuntimeConfig(repo, _settings())  # type: ignore[arg-type]
    await rc.refresh()
    assert rc.mcp_api_key() is None

    await repo.set_mcp_api_key("brand-new")
    # Before refresh, the snapshot is stale.
    assert rc.mcp_api_key() is None
    await rc.refresh()
    assert rc.mcp_api_key() == "brand-new"


@pytest.mark.asyncio
async def test_invalidate_keeps_last_known_until_refresh() -> None:
    repo = _FakeAppConfigRepo(_row(mcp_api_key="live"))
    rc = RuntimeConfig(repo, _settings())  # type: ignore[arg-type]
    await rc.refresh()
    rc.invalidate()
    # Still serves the last-known key — never briefly opens auth.
    assert rc.mcp_api_key() == "live"
    assert rc.loaded is False


@pytest.mark.asyncio
async def test_mcp_key_hint_masks_all_but_last_four() -> None:
    repo = _FakeAppConfigRepo(_row(mcp_api_key="abcdefgh1234"))
    rc = RuntimeConfig(repo, _settings())  # type: ignore[arg-type]
    await rc.refresh()
    assert rc.mcp_key_hint() == "••••1234"


@pytest.mark.asyncio
async def test_mcp_key_hint_none_when_unconfigured() -> None:
    rc = RuntimeConfig(_FakeAppConfigRepo(None), _settings())  # type: ignore[arg-type]
    await rc.refresh()
    assert rc.mcp_key_hint() is None


# ---------------------------------------------------------------------------
# Seed-from-env (no-lockout on first boot)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_seed_writes_env_keys_when_app_config_empty() -> None:
    repo = _FakeAppConfigRepo(None)
    settings = _settings(mcp_api_key="env-mcp", openai_api_key="sk-env")
    seeded = await _seed_app_config_from_env(repo, settings)  # type: ignore[arg-type]
    assert seeded is True
    row = await repo.get()
    assert row is not None
    assert row.mcp_api_key == "env-mcp"
    assert row.openai_api_key == "sk-env"


@pytest.mark.asyncio
async def test_seed_is_noop_when_app_config_already_populated() -> None:
    repo = _FakeAppConfigRepo(_row(mcp_api_key="existing"))
    settings = _settings(mcp_api_key="env-mcp")
    seeded = await _seed_app_config_from_env(repo, settings)  # type: ignore[arg-type]
    assert seeded is False
    row = await repo.get()
    assert row is not None
    assert row.mcp_api_key == "existing"  # never overwritten


@pytest.mark.asyncio
async def test_seed_is_noop_when_env_empty() -> None:
    repo = _FakeAppConfigRepo(None)
    seeded = await _seed_app_config_from_env(repo, _settings())  # type: ignore[arg-type]
    assert seeded is False
    assert await repo.get() is None  # stays fully env-driven, no row
