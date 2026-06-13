"""Config + key-management endpoint tests.

Covers: GET masks keys; generate returns once + bootstrap-open-then-locked;
rotate always requires the key; openai-key set/clear + validation;
embedding-mode; complete-onboarding. The router writes through a real
AppConfigRepository surface (faked in-memory) and the auth dependency
reads the SAME RuntimeConfig attached to app.state — so these also prove
the auth-reads-runtime integration end to end.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import config as config_router
from core.config import Settings, get_settings
from core.config_runtime import RuntimeConfig
from storage.app_config_repo import AppConfigRow


class _FakeAppConfigRepo:
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
        self._row = AppConfigRow(id=1, updated_at=datetime.now(UTC), **merged)  # type: ignore[arg-type]
        return self._row

    async def set_mcp_api_key(self, key: str | None) -> AppConfigRow:
        return await self.upsert(mcp_api_key=key)

    async def set_openai_api_key(self, key: str | None) -> AppConfigRow:
        return await self.upsert(openai_api_key=key)

    async def set_embedding_mode(self, mode: str) -> AppConfigRow:
        return await self.upsert(embedding_mode=mode)

    async def set_onboarding_completed(self, done: bool) -> AppConfigRow:
        return await self.upsert(onboarding_completed=done)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_app(repo: _FakeAppConfigRepo) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        rc = RuntimeConfig(repo, Settings())  # type: ignore[arg-type]
        await rc.refresh()
        app.state.runtime_config = rc
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(config_router.router)
    return app


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------
def test_get_config_unconfigured() -> None:
    app = _make_app(_FakeAppConfigRepo(None))
    with TestClient(app) as client:
        body = client.get("/config").json()
    assert body["configured"] is False
    assert body["onboarding_completed"] is False
    assert body["embedding_mode"] == "openai"
    assert body["embeddings_enabled"] is False
    assert body["has_openai_key"] is False
    assert body["mcp_key_hint"] is None


def test_get_config_masks_keys_never_returns_raw() -> None:
    repo = _FakeAppConfigRepo(
        AppConfigRow(
            id=1, mcp_api_key="supersecretkey1234", openai_api_key="sk-secret",
            embedding_mode="openai", embedding_model=None,
            onboarding_completed=True, updated_at=datetime.now(UTC),
        )
    )
    app = _make_app(repo)
    with TestClient(app) as client:
        resp = client.get("/config")
    body = resp.json()
    assert body["configured"] is True
    assert body["has_openai_key"] is True
    assert body["mcp_key_hint"] == "••••1234"
    # The raw keys must NEVER appear anywhere in the response.
    assert "supersecretkey1234" not in resp.text
    assert "sk-secret" not in resp.text


# ---------------------------------------------------------------------------
# generate: bootstrap-open-then-locked
# ---------------------------------------------------------------------------
def test_generate_returns_key_once_when_unconfigured() -> None:
    repo = _FakeAppConfigRepo(None)
    app = _make_app(repo)
    with TestClient(app) as client:
        resp = client.post("/config/mcp-key/generate")
        assert resp.status_code == 200
        key = resp.json()["api_key"]
        assert len(key) > 20
        # Now configured: GET reflects it (masked) and the key enforces auth.
        cfg = client.get("/config").json()
        assert cfg["configured"] is True
        assert cfg["mcp_key_hint"] == "••••" + key[-4:]


def test_generate_locked_after_configured_without_key_is_401() -> None:
    repo = _FakeAppConfigRepo(None)
    app = _make_app(repo)
    with TestClient(app) as client:
        first = client.post("/config/mcp-key/generate").json()["api_key"]
        # Second generate WITHOUT the key → 401 (now configured).
        locked = client.post("/config/mcp-key/generate")
        assert locked.status_code == 401
        # WITH the key → 200, new key minted.
        ok = client.post(
            "/config/mcp-key/generate", headers={"X-API-Key": first}
        )
        assert ok.status_code == 200
        assert ok.json()["api_key"] != first


# ---------------------------------------------------------------------------
# rotate: always requires the key
# ---------------------------------------------------------------------------
def test_rotate_requires_key_even_right_after_generate() -> None:
    repo = _FakeAppConfigRepo(None)
    app = _make_app(repo)
    with TestClient(app) as client:
        key = client.post("/config/mcp-key/generate").json()["api_key"]
        # No key → 401.
        assert client.post("/config/mcp-key/rotate").status_code == 401
        # Wrong key → 401.
        assert client.post(
            "/config/mcp-key/rotate", headers={"X-API-Key": "nope"}
        ).status_code == 401
        # Correct key → 200 + a different key.
        rotated = client.post(
            "/config/mcp-key/rotate", headers={"X-API-Key": key}
        )
        assert rotated.status_code == 200
        assert rotated.json()["api_key"] != key


def test_rotate_when_unconfigured_is_conflict() -> None:
    app = _make_app(_FakeAppConfigRepo(None))
    with TestClient(app) as client:
        assert client.post("/config/mcp-key/rotate").status_code == 409


# ---------------------------------------------------------------------------
# openai-key set/clear + validation
# ---------------------------------------------------------------------------
def test_set_openai_key_validates_prefix() -> None:
    app = _make_app(_FakeAppConfigRepo(None))
    with TestClient(app) as client:
        bad = client.post("/config/openai-key", json={"api_key": "not-a-key"})
        assert bad.status_code == 400
        ok = client.post("/config/openai-key", json={"api_key": "sk-live-xyz"})
        assert ok.status_code == 200
        cfg = client.get("/config").json()
        assert cfg["has_openai_key"] is True
        assert cfg["embeddings_enabled"] is True


def test_clear_openai_key_with_null() -> None:
    repo = _FakeAppConfigRepo(
        AppConfigRow(
            id=1, mcp_api_key=None, openai_api_key="sk-existing",
            embedding_mode="openai", embedding_model=None,
            onboarding_completed=False, updated_at=datetime.now(UTC),
        )
    )
    app = _make_app(repo)
    with TestClient(app) as client:
        assert client.get("/config").json()["has_openai_key"] is True
        resp = client.post("/config/openai-key", json={"api_key": None})
        assert resp.status_code == 200
        assert client.get("/config").json()["has_openai_key"] is False


# ---------------------------------------------------------------------------
# embedding-mode + complete-onboarding
# ---------------------------------------------------------------------------
def test_set_embedding_mode_validates_and_persists() -> None:
    app = _make_app(_FakeAppConfigRepo(None))
    with TestClient(app) as client:
        assert client.post(
            "/config/embedding-mode", json={"mode": "bogus"}
        ).status_code == 400
        assert client.post(
            "/config/embedding-mode", json={"mode": "local"}
        ).status_code == 200
        assert client.get("/config").json()["embedding_mode"] == "local"


def test_complete_onboarding_sets_flag() -> None:
    app = _make_app(_FakeAppConfigRepo(None))
    with TestClient(app) as client:
        assert client.post("/config/complete-onboarding").status_code == 200
        assert client.get("/config").json()["onboarding_completed"] is True


# ---------------------------------------------------------------------------
# Bootstrap-or-authed lock applies to openai-key + mode once configured
# ---------------------------------------------------------------------------
def test_openai_key_locked_after_configured() -> None:
    repo = _FakeAppConfigRepo(None)
    app = _make_app(repo)
    with TestClient(app) as client:
        key = client.post("/config/mcp-key/generate").json()["api_key"]
        # Configured now → unauthenticated openai-key set is 401.
        assert client.post(
            "/config/openai-key", json={"api_key": "sk-x"}
        ).status_code == 401
        assert client.post(
            "/config/openai-key",
            json={"api_key": "sk-x"},
            headers={"X-API-Key": key},
        ).status_code == 200
