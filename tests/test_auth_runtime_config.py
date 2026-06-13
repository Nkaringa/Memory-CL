"""The MCP auth dependency must read RuntimeConfig (Postgres-over-env).

Critical no-lockout proof: a key set in app_config enforces auth even
when the env MCP_API_KEY is empty (the runtime path), and — conversely —
when no RuntimeConfig is attached at all, the dependency still falls back
to env exactly as before (legacy path, every existing mcp-router test).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from apps.mcp import mcp_router
from core.config import Settings, get_settings
from core.config_runtime import RuntimeConfig
from core.mcp.execution import ExecutionContext, ToolRegistry
from storage.app_config_repo import AppConfigRow


class _EchoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    msg: str


class _EchoTool:
    name = "echo"
    request_schema = _EchoRequest

    async def execute(self, request: _EchoRequest, ctx: ExecutionContext) -> dict[str, Any]:
        return {"echoed": request.msg}


class _FakeAppConfigRepo:
    def __init__(self, row: AppConfigRow | None) -> None:
        self._row = row

    async def get(self) -> AppConfigRow | None:
        return self._row


def _row(mcp_api_key: str | None) -> AppConfigRow:
    return AppConfigRow(
        id=1, mcp_api_key=mcp_api_key, openai_api_key=None,
        embedding_mode="openai", embedding_model=None,
        onboarding_completed=False, updated_at=datetime.now(UTC),
    )


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_app(runtime: RuntimeConfig | None) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        r = ToolRegistry()
        r.register(_EchoTool())
        app.state.mcp_registry = r
        app.state.app_state = None
        if runtime is not None:
            app.state.runtime_config = runtime
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(mcp_router)
    return app


@pytest.mark.asyncio
async def test_auth_enforced_from_runtime_config_when_env_empty() -> None:
    """Env has NO MCP key, but app_config does → auth is enforced."""
    repo = _FakeAppConfigRepo(_row("pg-only-key"))
    rc = RuntimeConfig(repo, Settings(mcp_api_key=None))  # type: ignore[arg-type]
    await rc.refresh()
    app = _make_app(rc)

    with TestClient(app) as client:
        # No key → 401 (enforced purely from app_config).
        assert client.post("/mcp/tools/echo", json={"msg": "x"}).status_code == 401
        # Correct runtime key → 200.
        ok = client.post(
            "/mcp/tools/echo", json={"msg": "x"},
            headers={"X-API-Key": "pg-only-key"},
        )
        assert ok.status_code == 200
        # Wrong key → 401.
        assert client.post(
            "/mcp/tools/echo", json={"msg": "x"},
            headers={"X-API-Key": "WRONG"},
        ).status_code == 401


@pytest.mark.asyncio
async def test_runtime_config_unset_key_falls_back_to_env() -> None:
    """app_config has NO key but env does → env key enforces auth (the
    seed-not-yet-run window must never open a configured deployment)."""
    repo = _FakeAppConfigRepo(_row(None))
    rc = RuntimeConfig(repo, Settings(mcp_api_key="env-key"))  # type: ignore[arg-type]
    await rc.refresh()
    app = _make_app(rc)

    with TestClient(app) as client:
        assert client.post("/mcp/tools/echo", json={"msg": "x"}).status_code == 401
        ok = client.post(
            "/mcp/tools/echo", json={"msg": "x"},
            headers={"Authorization": "Bearer env-key"},
        )
        assert ok.status_code == 200


@pytest.mark.asyncio
async def test_no_runtime_config_uses_env_legacy_path() -> None:
    """No RuntimeConfig attached at all → dependency falls back to env
    Settings, the exact pre-onboarding behavior."""
    app = _make_app(None)
    with TestClient(app) as client:
        # Dev mode (no env key) → open.
        assert client.post("/mcp/tools/echo", json={"msg": "x"}).status_code == 200


@pytest.mark.asyncio
async def test_runtime_open_when_neither_pg_nor_env_set() -> None:
    repo = _FakeAppConfigRepo(_row(None))
    rc = RuntimeConfig(repo, Settings(mcp_api_key=None))  # type: ignore[arg-type]
    await rc.refresh()
    app = _make_app(rc)
    with TestClient(app) as client:
        assert client.post("/mcp/tools/echo", json={"msg": "x"}).status_code == 200
