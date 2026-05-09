"""Smoke tests for ``scripts/mcp_bridge.py``.

The bridge is a thin REST proxy + stdio MCP server. We don't try to
drive the stdio side here — that requires a real MCP client. Instead
we exercise the proxy class directly:

    * ``MemoryCLRemote.list_tools`` hits ``GET /mcp/tools``
    * ``MemoryCLRemote.call_tool`` hits ``POST /mcp/tools/{name}``
    * Failures degrade into the canonical ToolResponse-failed envelope

These checks lock in the contract the bridge promises to its stdio
clients without depending on the MCP SDK at all (so the suite passes
on CI hosts that haven't rebuilt against the new lockfile yet).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest


@pytest.fixture(scope="session")
def bridge_module():
    """Load ``scripts/mcp_bridge.py`` as a module without invoking ``main``."""
    repo_root = Path(__file__).resolve().parent.parent
    path = repo_root / "scripts" / "mcp_bridge.py"
    spec = importlib.util.spec_from_file_location("memcl_bridge_under_test", path)
    if spec is None or spec.loader is None:
        pytest.skip("could not load scripts/mcp_bridge.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client_with(handler) -> httpx.AsyncClient:
    """Build an httpx AsyncClient backed by an in-process MockTransport."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(base_url="http://memcl.test", transport=transport)


# ---------------------------------------------------------------------------
# list_tools — happy path
# ---------------------------------------------------------------------------
def test_remote_list_tools_returns_registry_entries(bridge_module) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mcp/tools"
        return httpx.Response(200, json={
            "tools": [
                {"name": "get_context", "request_schema": "GetContextRequest"},
                {"name": "query_graph", "request_schema": "QueryGraphRequest"},
            ],
        })

    remote = bridge_module.MemoryCLRemote.__new__(bridge_module.MemoryCLRemote)
    remote._client = _client_with(handler)
    remote._base = "http://memcl.test"
    remote._timeout = 5.0

    tools = asyncio.run(remote.list_tools())
    assert [t["name"] for t in tools] == ["get_context", "query_graph"]


# ---------------------------------------------------------------------------
# call_tool — happy path forwards the envelope verbatim
# ---------------------------------------------------------------------------
def test_remote_call_tool_forwards_envelope(bridge_module) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/mcp/tools/get_context"
        return httpx.Response(200, json={
            "tool": "get_context",
            "request_id": "abc123",
            "status": "success",
            "data": {"packet": {"task": "x"}},
            "error": None,
            "error_code": None,
            "latency_ms": 4.2,
        })

    remote = bridge_module.MemoryCLRemote.__new__(bridge_module.MemoryCLRemote)
    remote._client = _client_with(handler)
    remote._base = "http://memcl.test"
    remote._timeout = 5.0

    envelope = asyncio.run(remote.call_tool("get_context", {"task": "x"}))
    assert envelope["status"] == "success"
    assert envelope["data"]["packet"]["task"] == "x"
    assert envelope["request_id"] == "abc123"


# ---------------------------------------------------------------------------
# call_tool — non-200 response is converted to the canonical failed envelope
# ---------------------------------------------------------------------------
def test_remote_call_tool_handles_http_error(bridge_module) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    remote = bridge_module.MemoryCLRemote.__new__(bridge_module.MemoryCLRemote)
    remote._client = _client_with(handler)
    remote._base = "http://memcl.test"
    remote._timeout = 5.0

    envelope = asyncio.run(remote.call_tool("get_context", {"task": "x"}))
    assert envelope["status"] == "failed"
    assert envelope["error_code"] == "backend_error"
    assert "503" in envelope["error"]


# ---------------------------------------------------------------------------
# Auth header is set when MEMORYCL_API_KEY is configured (constructor path)
#
# We assert on the *constructor's product* (the underlying client's default
# headers) rather than making a request. Cleaner and avoids the brittleness
# of mocking ``httpx`` after the bridge has already imported it.
# ---------------------------------------------------------------------------
def test_remote_sends_x_api_key_header(bridge_module) -> None:
    remote = bridge_module.MemoryCLRemote(
        base_url="http://memcl.test",
        api_key="bridge-test-key",
        timeout=5.0,
    )
    try:
        # httpx normalizes header names to lowercase on its Headers object.
        assert remote._client.headers.get("x-api-key") == "bridge-test-key"
    finally:
        # The constructor opens an AsyncClient — close it cleanly so the
        # test doesn't leak the underlying socket pool.
        asyncio.run(remote.aclose())


def test_remote_sets_no_api_key_header_in_dev_mode(bridge_module) -> None:
    """When ``MEMORYCL_API_KEY`` is unset the bridge MUST send no auth header."""
    remote = bridge_module.MemoryCLRemote(
        base_url="http://memcl.test",
        api_key=None,
        timeout=5.0,
    )
    try:
        assert "x-api-key" not in remote._client.headers
    finally:
        asyncio.run(remote.aclose())
