"""Tests for the native MCP server adapter.

We exercise three layers:

1. **Handler logic** — call the module-level coroutines
   ``_handle_list_tools`` / ``_handle_call_tool`` directly. This is
   the same code path the decorator shims register with
   ``mcp.server.Server`` — testing it as plain async functions
   avoids depending on any SDK-internal wrapper layout.

2. **Server wiring** — verify ``build_native_mcp_server`` returns a
   real ``Server`` instance with the expected name + version, so a
   regression in the factory is caught.

3. **Auth middleware** — drive ``McpApiKeyMiddleware`` against a
   fake ASGI inner app and assert the same key-or-Bearer rule the
   REST surface enforces.

If the ``mcp`` SDK is not installed (e.g. CI hasn't been rebuilt
after the lockfile bump), each test that imports it is skipped.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from core.config import get_settings
from core.mcp.execution import ExecutionContext, ToolExecutor, ToolRegistry

# All MCP-SDK-dependent imports are guarded so the rest of the suite
# is unaffected by a missing dep on the build host.
mcp = pytest.importorskip("mcp", reason="mcp SDK not installed in this venv")
mcp_types = pytest.importorskip("mcp.types")


# ---------------------------------------------------------------------------
# Test fixtures — a minimal in-memory tool that echoes its payload.
# ---------------------------------------------------------------------------
class _EchoRequest(BaseModel):
    """Echo the input back."""
    model_config = ConfigDict(extra="forbid")
    msg: str


class _EchoTool:
    """Echoes the request's `msg` field back inside the response data."""

    name: str = "echo"
    request_schema = _EchoRequest

    async def execute(self, request: _EchoRequest, ctx: ExecutionContext) -> dict[str, Any]:
        return {"echoed": request.msg}


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _build_executor() -> tuple[ToolExecutor, ToolRegistry]:
    """Construct a fresh registry holding only Echo, plus its executor."""
    registry = ToolRegistry()
    registry.register(_EchoTool())
    return ToolExecutor(registry), registry


# ---------------------------------------------------------------------------
# 1. tools/list — names + JSON-schema present
# ---------------------------------------------------------------------------
def test_native_list_tools_advertises_each_registered_tool() -> None:
    """Every registry entry surfaces as an MCP `Tool` with a JSON Schema."""
    from apps.mcp.native_server import _handle_list_tools

    _, registry = _build_executor()
    tools = asyncio.run(_handle_list_tools(registry))

    names = sorted(t.name for t in tools)
    assert names == ["echo"]
    [tool] = tools
    # The MCP wire shape mandates an inputSchema dict.
    assert isinstance(tool.inputSchema, dict)
    assert tool.inputSchema.get("type") == "object"
    # The Pydantic-derived schema MUST advertise the required field.
    properties = tool.inputSchema.get("properties") or {}
    assert "msg" in properties


# ---------------------------------------------------------------------------
# 2. tools/call — successful invocation returns the canonical envelope
# ---------------------------------------------------------------------------
def test_native_call_tool_returns_success_envelope() -> None:
    from apps.mcp.native_server import _handle_call_tool

    executor, _ = _build_executor()
    blocks = asyncio.run(
        _handle_call_tool(executor, lambda: None, "echo", {"msg": "hello"})
    )

    [block] = blocks
    payload = json.loads(block.text)
    assert payload["tool"] == "echo"
    assert payload["status"] == "success"
    assert payload["data"]["echoed"] == "hello"
    # The envelope MUST carry a request_id so log lines correlate.
    assert isinstance(payload["request_id"], str) and payload["request_id"]


# ---------------------------------------------------------------------------
# 3. tools/call — unknown tool → structured failure (NOT an exception)
# ---------------------------------------------------------------------------
def test_native_call_unknown_tool_returns_failed_envelope() -> None:
    from apps.mcp.native_server import _handle_call_tool

    executor, _ = _build_executor()
    blocks = asyncio.run(
        _handle_call_tool(executor, lambda: None, "does-not-exist", {})
    )
    payload = json.loads(blocks[0].text)
    assert payload["status"] == "failed"
    assert payload["error_code"] == "unknown_tool"


# ---------------------------------------------------------------------------
# 4. tools/call — malformed payload → structured validation failure
# ---------------------------------------------------------------------------
def test_native_call_validation_error_surfaces_in_envelope() -> None:
    from apps.mcp.native_server import _handle_call_tool

    executor, _ = _build_executor()
    # `_EchoRequest` requires `msg` (string). Empty payload must fail.
    blocks = asyncio.run(_handle_call_tool(executor, lambda: None, "echo", {}))
    payload = json.loads(blocks[0].text)
    assert payload["status"] == "failed"
    assert payload["error_code"] == "validation_error"


# ---------------------------------------------------------------------------
# 5. Determinism — repeated calls with the same arguments yield identical
#    DATA bytes (request_id and latency are intentionally per-call).
# ---------------------------------------------------------------------------
def test_native_call_tool_is_deterministic_for_same_input() -> None:
    from apps.mcp.native_server import _handle_call_tool

    executor, _ = _build_executor()
    payload_a = json.loads(asyncio.run(
        _handle_call_tool(executor, lambda: None, "echo", {"msg": "x"})
    )[0].text)
    payload_b = json.loads(asyncio.run(
        _handle_call_tool(executor, lambda: None, "echo", {"msg": "x"})
    )[0].text)

    # Per-call fields differ; the deterministic surface (data + status
    # + tool name + error fields) MUST match exactly.
    for key in ("tool", "status", "data", "error", "error_code"):
        assert payload_a[key] == payload_b[key]


# ---------------------------------------------------------------------------
# 6. Server factory — name + version handshake values
# ---------------------------------------------------------------------------
def test_build_native_mcp_server_returns_named_server() -> None:
    """``build_native_mcp_server`` returns a Server with our identity."""
    from apps.mcp.native_server import (
        SERVER_NAME,
        SERVER_VERSION,
        build_native_mcp_server,
    )

    executor, registry = _build_executor()
    server = build_native_mcp_server(
        registry=registry,
        executor=executor,
        get_app_state=lambda: None,
    )
    # The server name surfaces in the MCP `initialize` handshake.
    assert server.name == SERVER_NAME
    assert SERVER_VERSION  # non-empty


# ===========================================================================
# Auth middleware — exercises apps/mcp/native_auth.py against a fake ASGI
# ===========================================================================
class _FakeAsgi:
    """Records the most recent scope it was invoked with."""

    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.invocations.append(scope)
        # Minimal 200 OK so the test can verify pass-through.
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _drive_auth(scope: dict[str, Any], inner: _FakeAsgi) -> tuple[int, dict[str, str]]:
    """Run `McpApiKeyMiddleware` once and return (status, headers)."""
    from apps.mcp.native_auth import McpApiKeyMiddleware

    middleware = McpApiKeyMiddleware(inner)

    sent_messages: list[dict[str, Any]] = []

    async def _receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def _send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    await middleware(scope, _receive, _send)

    start = next(m for m in sent_messages if m["type"] == "http.response.start")
    headers = {
        k.decode("latin-1"): v.decode("latin-1")
        for k, v in start.get("headers", [])
    }
    return start["status"], headers


def test_auth_dev_mode_passes_through_when_no_key_configured(monkeypatch) -> None:
    """No `MCP_API_KEY` set → middleware is a no-op."""
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    get_settings.cache_clear()

    inner = _FakeAsgi()
    scope = {"type": "http", "headers": []}
    status, _ = asyncio.run(_drive_auth(scope, inner))
    assert status == 200
    assert len(inner.invocations) == 1


def test_auth_rejects_missing_key_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "phase11-test-key")
    get_settings.cache_clear()

    inner = _FakeAsgi()
    scope = {"type": "http", "headers": []}
    status, headers = asyncio.run(_drive_auth(scope, inner))
    assert status == 401
    assert headers.get("www-authenticate") == "Bearer"
    assert len(inner.invocations) == 0


def test_auth_accepts_x_api_key(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "phase11-test-key")
    get_settings.cache_clear()

    inner = _FakeAsgi()
    scope = {
        "type": "http",
        "headers": [(b"x-api-key", b"phase11-test-key")],
    }
    status, _ = asyncio.run(_drive_auth(scope, inner))
    assert status == 200


def test_auth_accepts_authorization_bearer(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "phase11-test-key")
    get_settings.cache_clear()

    inner = _FakeAsgi()
    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer phase11-test-key")],
    }
    status, _ = asyncio.run(_drive_auth(scope, inner))
    assert status == 200


def test_auth_rejects_wrong_key(monkeypatch) -> None:
    monkeypatch.setenv("MCP_API_KEY", "phase11-test-key")
    get_settings.cache_clear()

    inner = _FakeAsgi()
    scope = {
        "type": "http",
        "headers": [(b"x-api-key", b"wrong-key")],
    }
    status, _ = asyncio.run(_drive_auth(scope, inner))
    assert status == 401
