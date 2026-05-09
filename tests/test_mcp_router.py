from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from apps.mcp import mcp_router
from core.config import get_settings
from core.mcp.execution import ExecutionContext, ToolRegistry


class _EchoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    msg: str


class _EchoTool:
    name: str = "echo"
    request_schema = _EchoRequest

    async def execute(self, request: _EchoRequest, ctx: ExecutionContext) -> dict[str, Any]:
        return {"echoed": request.msg, "request_id": ctx.request_id}


def _make_app(*, registry: ToolRegistry, app_state: Any = None) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        app.state.mcp_registry = registry
        app.state.app_state = app_state
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(mcp_router)
    return app


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---- /mcp/tools -----------------------------------------------------------
def test_list_tools_returns_registered_names() -> None:
    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r)
    with TestClient(app) as client:
        resp = client.get("/mcp/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert [t["name"] for t in body["tools"]] == ["echo"]
    assert body["tools"][0]["request_schema"] == "_EchoRequest"


# ---- /mcp/tools/{tool} success path --------------------------------------
def test_invoke_tool_success() -> None:
    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r)
    with TestClient(app) as client:
        resp = client.post("/mcp/tools/echo", json={"msg": "hi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["data"]["echoed"] == "hi"
    assert body["tool"] == "echo"


# ---- failure surfaces -----------------------------------------------------
def test_invoke_unknown_tool_returns_failed_response() -> None:
    app = _make_app(registry=ToolRegistry())
    with TestClient(app) as client:
        resp = client.post("/mcp/tools/missing", json={})
    assert resp.status_code == 200          # spec: failures are in-band
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "unknown_tool"


def test_invoke_with_invalid_payload_returns_validation_error() -> None:
    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r)
    with TestClient(app) as client:
        resp = client.post("/mcp/tools/echo", json={"unexpected": "x"})
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "validation_error"


# ---- auth -----------------------------------------------------------------
def test_auth_no_op_when_no_key_configured() -> None:
    """Dev mode: mcp_api_key not set → all requests allowed."""
    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r)
    with TestClient(app) as client:
        assert client.post("/mcp/tools/echo", json={"msg": "x"}).status_code == 200


def test_auth_rejects_request_without_api_key() -> None:
    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r)

    with patch.dict("os.environ", {"MCP_API_KEY": "secret-123"}):
        get_settings.cache_clear()
        with TestClient(app) as client:
            resp = client.post("/mcp/tools/echo", json={"msg": "x"})
    assert resp.status_code == 401


def test_auth_accepts_x_api_key_header() -> None:
    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r)

    with patch.dict("os.environ", {"MCP_API_KEY": "secret-123"}):
        get_settings.cache_clear()
        with TestClient(app) as client:
            resp = client.post(
                "/mcp/tools/echo",
                json={"msg": "x"},
                headers={"X-API-Key": "secret-123"},
            )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_auth_accepts_bearer_token() -> None:
    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r)

    with patch.dict("os.environ", {"MCP_API_KEY": "secret-123"}):
        get_settings.cache_clear()
        with TestClient(app) as client:
            resp = client.post(
                "/mcp/tools/echo",
                json={"msg": "x"},
                headers={"Authorization": "Bearer secret-123"},
            )
    assert resp.status_code == 200


def test_auth_rejects_wrong_key() -> None:
    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r)

    with patch.dict("os.environ", {"MCP_API_KEY": "secret-123"}):
        get_settings.cache_clear()
        with TestClient(app) as client:
            resp = client.post(
                "/mcp/tools/echo",
                json={"msg": "x"},
                headers={"X-API-Key": "WRONG"},
            )
    assert resp.status_code == 401
