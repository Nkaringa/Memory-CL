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
from core.governance import AuditLogger
from core.mcp.execution import ExecutionContext, ToolRegistry


class _EchoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    msg: str


class _EchoTool:
    name: str = "echo"
    request_schema = _EchoRequest

    async def execute(self, request: _EchoRequest, ctx: ExecutionContext) -> dict[str, Any]:
        return {"echoed": request.msg, "request_id": ctx.request_id}


def _make_app(
    *,
    registry: ToolRegistry,
    app_state: Any = None,
    audit_logger: Any = None,
) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        app.state.mcp_registry = registry
        app.state.app_state = app_state
        if audit_logger is not None:
            app.state.audit_logger = audit_logger
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
    # Full JSON Schema is exposed under "schema" alongside the class name.
    schema = body["tools"][0]["schema"]
    assert set(schema["properties"]) == {"msg"}
    assert schema["required"] == ["msg"]


def test_list_tools_exposes_json_schema_for_default_registry() -> None:
    from apps.mcp.registry import build_default_registry

    app = _make_app(registry=build_default_registry())
    with TestClient(app) as client:
        resp = client.get("/mcp/tools")
    assert resp.status_code == 200
    by_name = {t["name"]: t for t in resp.json()["tools"]}

    qg = by_name["query_graph"]
    assert qg["request_schema"] == "QueryGraphRequest"  # compat string kept
    schema = qg["schema"]
    assert {"node", "repo_id", "depth"} <= set(schema["properties"])
    assert set(schema["required"]) == {"node", "repo_id"}
    # Optional field defaults survive the round-trip.
    assert schema["properties"]["depth"]["default"] == 1


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


# ---- audit chain ------------------------------------------------------------
def test_invoke_tool_success_appends_audit_entry() -> None:
    r = ToolRegistry()
    r.register(_EchoTool())
    audit = AuditLogger()
    app = _make_app(registry=r, audit_logger=audit)
    with TestClient(app) as client:
        resp = client.post("/mcp/tools/echo", json={"msg": "hi"})
    assert resp.status_code == 200
    body = resp.json()

    assert len(audit.store) == 1
    entry = audit.store.tail()
    assert entry is not None
    payload = entry.payload
    assert payload["actor"] == "agent"
    md = payload["metadata"]
    assert md["tool"] == "echo"
    assert md["request_id"] == body["request_id"]
    assert md["status"] == "success"
    assert md["latency_ms"] >= 0
    assert md["authenticated"] is False  # dev mode — no key configured
    assert audit.verify() is True


def test_invoke_tool_failure_appends_audit_entry() -> None:
    audit = AuditLogger()
    app = _make_app(registry=ToolRegistry(), audit_logger=audit)
    with TestClient(app) as client:
        resp = client.post("/mcp/tools/missing", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"

    assert len(audit.store) == 1
    md = audit.store.tail().payload["metadata"]
    assert md["tool"] == "missing"
    assert md["status"] == "failed"
    assert audit.verify() is True


def test_audit_append_failure_does_not_break_tool_call() -> None:
    class _ExplodingLogger:
        def record(self, **kwargs: Any) -> None:
            raise RuntimeError("audit sink down")

    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r, audit_logger=_ExplodingLogger())
    with TestClient(app) as client:
        resp = client.post("/mcp/tools/echo", json={"msg": "still works"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_invoke_without_audit_logger_attached_still_works() -> None:
    r = ToolRegistry()
    r.register(_EchoTool())
    app = _make_app(registry=r)  # no app.state.audit_logger
    with TestClient(app) as client:
        resp = client.post("/mcp/tools/echo", json={"msg": "ok"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_audit_entry_never_contains_raw_api_key() -> None:
    import json

    r = ToolRegistry()
    r.register(_EchoTool())
    audit = AuditLogger()
    app = _make_app(registry=r, audit_logger=audit)

    with patch.dict("os.environ", {"MCP_API_KEY": "secret-123"}):
        get_settings.cache_clear()
        with TestClient(app) as client:
            resp = client.post(
                "/mcp/tools/echo",
                json={"msg": "x"},
                headers={"X-API-Key": "secret-123"},
            )
    assert resp.status_code == 200
    assert len(audit.store) == 1
    payload = audit.store.tail().payload
    assert "secret-123" not in json.dumps(payload)
    assert payload["metadata"]["authenticated"] is True


def test_invoked_tools_visible_via_audit_tail_and_verify() -> None:
    """End-to-end: tool invocations grow the chain that /audit/* reports on."""
    from unittest.mock import AsyncMock

    from apps.api.routers import audit as audit_routes
    from apps.api.state import AppState

    state = AppState.with_default_embedder(
        postgres=AsyncMock(), qdrant=AsyncMock(),
        neo4j=AsyncMock(), redis=AsyncMock(),
        units_repo=AsyncMock(), graph_repo=AsyncMock(), vector_repo=AsyncMock(),
        embedding_dimension=32,
    )
    r = ToolRegistry()
    r.register(_EchoTool())
    audit = AuditLogger()
    app = _make_app(registry=r, app_state=state, audit_logger=audit)
    app.include_router(audit_routes.router)

    with TestClient(app) as client:
        assert client.post("/mcp/tools/echo", json={"msg": "a"}).status_code == 200
        assert client.post("/mcp/tools/nope", json={}).status_code == 200

        tail = client.get("/audit/tail").json()
        assert tail["chain_length"] == 2
        tools = [e["payload"]["metadata"]["tool"] for e in tail["entries"]]
        assert tools == ["echo", "nope"]

        verify = client.get("/audit/verify").json()
        assert verify["intact"] is True
        assert verify["chain_length"] == 2


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
