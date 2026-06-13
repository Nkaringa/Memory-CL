"""Native MCP-protocol server adapter.

Wraps the existing `core.mcp.execution.ToolRegistry` + `ToolExecutor`
behind an `mcp.server.Server` instance so MCP-protocol clients
(stdio, SSE, streamable-HTTP) can talk to Memory-CL without going
through the REST layer.

DESIGN NOTES
============

This module is a thin adapter — it MUST NOT duplicate tool logic. The
existing executor remains the single source of truth for:

    * tool selection (by name)
    * input validation (against each tool's Pydantic request schema)
    * deterministic execution (Phase-2 → Phase-4 → context assembly)
    * structured error envelope on failure
    * audit / OTEL emission

What this adapter adds:

    * MCP protocol shape — JSON-Schema for each tool, `list_tools`
      and `call_tool` handlers
    * a small wall-clock OTEL span around each MCP call
    * a request-id allocated at the transport boundary so log lines
      can be correlated end-to-end (transport → executor → tool)

WHY THE HANDLER LOGIC LIVES AT MODULE LEVEL
-------------------------------------------

The MCP SDK's ``@server.list_tools()`` / ``@server.call_tool()``
decorators wrap our user function in an outer protocol handler that
accepts a request object. That outer form is what ends up registered
in ``server.request_handlers``; our inner user function is captured
in a closure and not externally callable.

To keep the handler logic directly testable (and reusable elsewhere
in the future without poking at SDK internals), we define
``_handle_list_tools`` and ``_handle_call_tool`` at module level. The
decorator-registered functions inside ``build_native_mcp_server``
become two-line shims that just forward to them.

Runtime behavior is identical to inlining the logic inside the
decorator closures.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

# The MCP Python SDK is the canonical implementation of the protocol.
# We import lazily-named symbols so a dependency drift surfaces as a
# clear ImportError at module load instead of at request time.
from mcp.server import Server
from mcp.types import TextContent, Tool

from core.mcp.execution import ExecutionContext, ToolExecutor, ToolRegistry
from core.mcp.logevent import emit_mcp_event
from core.observability import get_tracer

_tracer = get_tracer("apps.mcp.native_server")

# Identifier surfaced in MCP `initialize` handshake. Clients show this
# in their server-picker UI.
SERVER_NAME = "memory-cl"
SERVER_VERSION = "0.1.0"

# Type alias for the AppState resolver — we accept a callable rather
# than a pinned reference so the same server instance can serve calls
# across the lifespan window (the AppState attribute is set after the
# server is built).
AppStateResolver = Callable[[], Any]


# ---------------------------------------------------------------------------
# Module-level handler logic — directly callable from tests + the bridge.
# ---------------------------------------------------------------------------
async def _handle_list_tools(registry: ToolRegistry) -> list[Tool]:
    """Translate every registered tool into an MCP ``Tool``.

    The MCP wire shape carries:

        * ``name``        — stable identifier
        * ``description`` — agent-facing summary (we use the tool
                            class docstring's first line if present)
        * ``inputSchema`` — JSON Schema describing the request body

    JSON schemas are derived directly from each tool's Pydantic
    request_schema so the contract stays in lock-step with the REST
    surface. No duplication.
    """
    return [_to_protocol_tool(reg_tool) for reg_tool in registry.all()]


async def _handle_call_tool(
    executor: ToolExecutor,
    get_app_state: AppStateResolver,
    name: str,
    arguments: dict[str, Any] | None,
) -> list[TextContent]:
    """Execute one tool call via the existing executor and return MCP content.

    We delegate verbatim to ``executor.execute`` — that path already
    runs schema validation, tool invocation, audit emission, and the
    canonical error envelope. The MCP-specific steps here are:

        1. Allocate a request_id at the transport boundary so logs +
           OTEL spans correlate end-to-end.
        2. Wrap the call in a transport-level OTEL span (the inner
           ``mcp.tool.execution`` span comes from the executor).
        3. Serialize the resulting ``ToolResponse`` envelope as
           canonical JSON inside a single MCP ``TextContent`` block.
    """
    request_id = uuid.uuid4().hex[:16]
    payload = arguments or {}
    start = time.perf_counter()

    with _tracer.start_as_current_span("mcp.native.request") as span:
        span.set_attribute("tool", name)
        span.set_attribute("request_id", request_id)
        span.set_attribute("transport", "mcp.native")

        ctx = ExecutionContext.new(
            state=get_app_state(),
            user_scope=None,  # transport-layer auth handles identity
            request_id=request_id,
        )
        response = await executor.execute(name, payload, ctx=ctx)

        elapsed_ms = (time.perf_counter() - start) * 1000
        span.set_attribute("status", response.status.value)
        span.set_attribute("latency_ms", elapsed_ms)

        emit_mcp_event(
            event="mcp_native_call",
            tool=name,
            request_id=request_id,
            status=response.status.value,
            latency_ms=elapsed_ms,
            user_scope=None,
            level="info" if response.status.value == "success" else "warning",
        )

    # Canonical JSON keeps two identical calls byte-deterministic
    # outside the request_id / latency_ms fields.
    return [
        TextContent(
            type="text",
            text=json.dumps(
                response.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Server factory — wires the module-level handlers into an `mcp.server.Server`.
# ---------------------------------------------------------------------------
def build_native_mcp_server(
    *,
    registry: ToolRegistry,
    executor: ToolExecutor,
    get_app_state: AppStateResolver,
) -> Server:
    """Construct an `mcp.server.Server` exposing every registered tool.

    Parameters
    ----------
    registry
        The shared ``ToolRegistry`` populated by ``build_default_registry``.
        We DO NOT touch this — we only read it.
    executor
        The shared ``ToolExecutor`` over the same registry. Reused so
        validation, span emission, and the structured-error envelope
        match REST behavior byte-for-byte.
    get_app_state
        Callable returning the live ``AppState``. Resolved per-call so
        the server captured at build time still sees the AppState
        attached during ``lifespan`` startup.

    Returns
    -------
    Server
        A configured MCP server. Mount it on a transport via
        ``apps/mcp/native_transport.py`` for FastAPI, or run it on
        stdio via the official ``stdio_server`` helper.
    """
    server: Server = Server(name=SERVER_NAME, version=SERVER_VERSION)

    @server.list_tools()
    async def _list_tools_shim() -> list[Tool]:
        return await _handle_list_tools(registry)

    @server.call_tool()
    async def _call_tool_shim(
        name: str, arguments: dict[str, Any] | None,
    ) -> list[TextContent]:
        return await _handle_call_tool(executor, get_app_state, name, arguments)

    return server


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_protocol_tool(reg_tool: Any) -> Tool:
    """Translate a Memory-CL ``Tool`` (Protocol) into an MCP ``Tool``.

    Description precedence (kept identical to the REST listing in
    ``apps/mcp/router.py``): the tool's explicit agent-facing
    ``description`` attribute, else the class docstring's first line,
    else the tool name.
    """
    explicit = getattr(reg_tool, "description", None)
    if isinstance(explicit, str) and explicit.strip():
        description = explicit.strip()
    else:
        description = (
            (reg_tool.__class__.__doc__ or reg_tool.name).strip().splitlines()[0]
        )
    schema = reg_tool.request_schema.model_json_schema()
    return Tool(
        name=reg_tool.name,
        description=description,
        inputSchema=schema,
    )


__all__ = [
    "SERVER_NAME",
    "SERVER_VERSION",
    "AppStateResolver",
    "_handle_call_tool",
    "_handle_list_tools",
    "build_native_mcp_server",
]
