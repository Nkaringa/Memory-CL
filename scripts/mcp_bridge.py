#!/usr/bin/env python3
"""Memory-CL stdio MCP bridge.

A thin local adapter that lets stdio-only MCP clients (Claude Desktop,
Claude Code, Cursor, Zed, etc.) talk to a remote Memory-CL instance
that exposes its native MCP server over HTTP/SSE.

WHAT IT DOES
============

    Claude Desktop ─┐  stdio (MCP protocol)
                    ▼
              [this bridge]
                    ▼  HTTP/JSON
              Memory-CL API ── /mcp/tools  /mcp/tools/{name}

The bridge advertises every tool the remote registry exposes, and
each ``tools/call`` is forwarded verbatim to the REST MCP surface.
It carries no business logic — the only thing it owns is the
stdio↔HTTP protocol gymnastics.

WHY REST AND NOT THE NATIVE SSE/HTTP TRANSPORT?
-----------------------------------------------
Two reasons:

1. The REST MCP surface (``GET /mcp/tools`` + ``POST /mcp/tools/{name}``)
   is older than the native MCP SDK transports and is the most stable
   contract Memory-CL ships. Pinning the bridge to it means the bridge
   keeps working across MCP SDK upgrades.

2. The bridge is meant to run on lightweight client machines (laptops)
   that may not have the ``mcp`` SDK at the same version as the server.
   Talking REST keeps the bridge dependency-light: only ``mcp`` (for
   stdio) and ``httpx`` (for forwarding).

CONFIGURATION
=============

    MEMORYCL_URL       (default: http://localhost:8000)
    MEMORYCL_API_KEY   (default: unset → no auth header sent)
    MEMORYCL_TIMEOUT   (default: 30)  seconds for HTTP calls

USAGE
=====

    python scripts/mcp_bridge.py

The bridge speaks MCP on stdin/stdout — it's not meant to be run
interactively. See ``docs/MCP_BRIDGE.md`` for client config snippets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

# Defer the heavy SDK imports to inside ``main`` so a misconfiguration
# (missing dep, wrong Python) shows up as a clean log line instead of a
# bare ImportError before logging is even configured.

LOG = logging.getLogger("memcl.bridge")

DEFAULT_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30.0


def _configure_logging() -> None:
    """Log to stderr — stdout is reserved for MCP protocol traffic."""
    level = os.environ.get("MEMORYCL_BRIDGE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s memcl-bridge %(levelname)s %(message)s",
        stream=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Remote client — speaks REST to Memory-CL
# ---------------------------------------------------------------------------
class MemoryCLRemote:
    """Tiny REST client over Memory-CL's REST MCP surface."""

    def __init__(self, *, base_url: str, api_key: str | None, timeout: float) -> None:
        import httpx

        self._base = base_url.rstrip("/")
        self._timeout = timeout
        headers: dict[str, str] = {"accept": "application/json"}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=self._base, timeout=timeout, headers=headers,
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        """Hit ``GET /mcp/tools`` — returns the registry's static list."""
        resp = await self._client.get("/mcp/tools")
        resp.raise_for_status()
        body = resp.json()
        return list(body.get("tools", []))

    async def call_tool(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Hit ``POST /mcp/tools/{name}`` — returns the ToolResponse envelope."""
        resp = await self._client.post(f"/mcp/tools/{name}", json=payload)
        # Memory-CL returns 200 even on tool failure; surface the body as-is.
        if resp.status_code != 200:
            return {
                "tool": name,
                "request_id": "",
                "status": "failed",
                "data": {},
                "error": f"HTTP {resp.status_code}: {resp.text[:512]}",
                "error_code": "backend_error",
                "latency_ms": 0.0,
            }
        return dict(resp.json())

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Tool-schema fetch — fall back to remote OpenAPI if the registry list
# doesn't carry inputSchema.
# ---------------------------------------------------------------------------
async def _fetch_input_schemas(
    remote: MemoryCLRemote, tool_names: list[str],
) -> dict[str, dict[str, Any]]:
    """Best-effort fetch of per-tool JSON schemas from the OpenAPI doc.

    Memory-CL's REST tool list returns ``{name, request_schema}`` (a
    pydantic class name string), not the full JSON schema. We extract
    the matching schemas from the OpenAPI document so MCP clients can
    advertise complete tool input shapes.

    On failure, we fall back to a permissive ``{}`` object schema —
    clients can still call the tool, the server validates the payload.
    """
    schemas: dict[str, dict[str, Any]] = {}
    try:
        resp = await remote._client.get("/openapi.json")
        if resp.status_code != 200:
            raise RuntimeError(f"openapi {resp.status_code}")
        openapi = resp.json()
        components = (openapi.get("components") or {}).get("schemas") or {}
    except Exception as exc:
        LOG.warning("openapi_fetch_failed: %s — falling back to permissive schemas", exc)
        return {n: {"type": "object"} for n in tool_names}

    # Crude but reliable: take the registry's `request_schema` class
    # name and look it up in components.schemas.
    list_resp = await remote._client.get("/mcp/tools")
    if list_resp.status_code == 200:
        for entry in list_resp.json().get("tools", []):
            cls_name = entry.get("request_schema")
            tool_name = entry.get("name")
            if not cls_name or not tool_name:
                continue
            schema = components.get(cls_name) or {"type": "object"}
            schemas[tool_name] = schema
    return schemas


# ---------------------------------------------------------------------------
# Bridge entry point
# ---------------------------------------------------------------------------
async def _run_bridge() -> None:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool

    base_url = os.environ.get("MEMORYCL_URL", DEFAULT_URL)
    api_key = os.environ.get("MEMORYCL_API_KEY")
    try:
        timeout = float(os.environ.get("MEMORYCL_TIMEOUT", str(DEFAULT_TIMEOUT)))
    except ValueError:
        timeout = DEFAULT_TIMEOUT

    LOG.info("bridge_start url=%s auth=%s", base_url, "yes" if api_key else "no")

    remote = MemoryCLRemote(base_url=base_url, api_key=api_key, timeout=timeout)

    # Sanity-ping the remote — early failure is much friendlier than a
    # confusing first-call error inside a stdio session.
    try:
        await remote.list_tools()
    except Exception as exc:
        LOG.error("remote_unreachable url=%s err=%s", base_url, exc)
        # Continue anyway — the client may want to surface the error
        # interactively rather than fail the whole MCP launch.

    server: Server = Server(name="memory-cl-bridge", version="0.1.0")

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        try:
            entries = await remote.list_tools()
        except Exception as exc:
            LOG.warning("list_tools_failed: %s", exc)
            return []
        names = [e.get("name") for e in entries if e.get("name")]
        schemas = await _fetch_input_schemas(remote, [n for n in names if n])
        return [
            Tool(
                name=n,
                description=f"Memory-CL tool '{n}' (proxied via bridge → {base_url})",
                inputSchema=schemas.get(n, {"type": "object"}),
            )
            for n in names
            if n
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        payload = arguments or {}
        try:
            envelope = await remote.call_tool(name, payload)
        except Exception as exc:
            LOG.warning("call_tool_failed name=%s err=%s", name, exc)
            envelope = {
                "tool": name,
                "request_id": "",
                "status": "failed",
                "data": {},
                "error": f"bridge: {type(exc).__name__}: {exc}",
                "error_code": "backend_error",
                "latency_ms": 0.0,
            }
        return [
            TextContent(
                type="text",
                text=json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            )
        ]

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options(),
            )
    finally:
        await remote.aclose()
        LOG.info("bridge_stop")


def main() -> int:
    _configure_logging()
    try:
        asyncio.run(_run_bridge())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        LOG.error("bridge_fatal: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
