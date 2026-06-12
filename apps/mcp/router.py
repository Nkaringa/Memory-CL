"""MCP router — REST surface mounted under /mcp.

Endpoints:
    GET  /mcp/tools                 — list registered tools
    POST /mcp/tools/{tool_name}     — invoke a tool

Auth is enforced on the POST path. The GET path is informational and
intentionally cheap so an agent can discover the surface without
authenticating; it returns only static metadata (no data leakage).
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from apps.mcp.auth import ApiKeyDep
from core.governance import AuditAction, AuditActor
from core.mcp.execution import ExecutionContext, ToolExecutor, ToolRegistry
from core.mcp.logevent import emit_mcp_event
from core.mcp.schemas import ToolResponse
from core.observability import get_tracer

_tracer = get_tracer("apps.mcp.router")
router = APIRouter(prefix="/mcp", tags=["mcp"])


class ToolsListEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    request_schema: str = Field(
        description="Pydantic class name of the tool's request schema",
    )
    # Named `json_schema` internally to avoid shadowing BaseModel.schema;
    # serialized as `"schema"` on the wire (FastAPI renders by alias).
    json_schema: dict[str, Any] = Field(
        serialization_alias="schema",
        description=(
            "Full JSON Schema of the request model "
            "(pydantic v2 model_json_schema output)"
        ),
    )


class ToolsListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[ToolsListEntry]


# Map MCP tools onto the closest spec-mandated AuditAction. Anything not
# listed here is a read-path tool → RETRIEVE.
_AUDIT_ACTION_BY_TOOL: dict[str, AuditAction] = {
    "ingest_repository": AuditAction.INGEST,
    "update_memory": AuditAction.UPDATE,
}


def _scope_marker(api_key: str | None) -> str:
    """Non-reversible marker for the presented API key.

    The audit chain is readable via /audit/tail, so the raw secret must
    never enter an entry payload — we record a short SHA-256 prefix that
    still distinguishes scopes without being recoverable.
    """
    if not api_key:
        return "anonymous"
    return "key:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def _append_audit(
    request: Request,
    *,
    tool_name: str,
    request_id: str,
    status: str,
    latency_ms: float,
    api_key: str | None,
) -> None:
    """Append a tool-invocation entry to the app's hash-chained audit log.

    Best-effort by design: audit appending must never break the tool call,
    so every failure path is swallowed into a warning event. When no
    `app.state.audit_logger` is attached (bare test apps), this is a no-op.
    """
    logger = getattr(request.app.state, "audit_logger", None)
    if logger is None:
        return
    try:
        logger.record(
            actor=AuditActor.AGENT,
            action=_AUDIT_ACTION_BY_TOOL.get(tool_name, AuditAction.RETRIEVE),
            entity_id=f"mcp:{tool_name}",
            tenant_id=_scope_marker(api_key),
            after=status,
            metadata={
                "tool": tool_name,
                "request_id": request_id,
                "status": status,
                "latency_ms": round(latency_ms, 3),
                "user_scope": _scope_marker(api_key),
                "authenticated": api_key is not None,
            },
            level="debug",
        )
    except Exception as exc:
        emit_mcp_event(
            event="mcp_audit_append_failed",
            tool=tool_name,
            request_id=request_id,
            status="failed",
            latency_ms=latency_ms,
            level="warning",
            error=f"{type(exc).__name__}: {exc}",
        )


def _registry(request: Request) -> ToolRegistry:
    """Pull the per-app ToolRegistry off `app.state`.

    The lifespan handler (apps/api/lifespan.py) is responsible for
    populating it; tests can do the same with a one-line setup.
    """
    reg = getattr(request.app.state, "mcp_registry", None)
    if reg is None:
        raise RuntimeError(
            "MCP ToolRegistry not initialised on app.state.mcp_registry"
        )
    assert isinstance(reg, ToolRegistry)
    return reg


@router.get("/tools", response_model=ToolsListResponse)
async def list_tools(
    registry: Annotated[ToolRegistry, Depends(_registry)],
) -> ToolsListResponse:
    return ToolsListResponse(
        tools=[
            ToolsListEntry(
                name=t.name,
                request_schema=t.request_schema.__name__,
                json_schema=t.request_schema.model_json_schema(),
            )
            for t in registry.all()
        ]
    )


@router.post(
    "/tools/{tool_name}",
    response_model=ToolResponse,
    status_code=status.HTTP_200_OK,
)
async def invoke_tool(
    tool_name: str,
    payload: dict[str, Any],
    request: Request,
    registry: Annotated[ToolRegistry, Depends(_registry)],
    api_key: ApiKeyDep,  # auth enforced here
) -> ToolResponse:
    """Invoke `tool_name` with a JSON body validated by its request schema.

    The endpoint always returns 200 even when the tool fails — failures
    are conveyed inside `ToolResponse` so a client can route on `status`
    without having to interpret HTTP error codes.
    """
    request_id = uuid.uuid4().hex[:16]
    start = time.perf_counter()

    with _tracer.start_as_current_span("mcp.server.request") as span:
        span.set_attribute("tool", tool_name)
        span.set_attribute("request_id", request_id)
        span.set_attribute("authenticated", api_key is not None)

        ctx = ExecutionContext.new(
            state=getattr(request.app.state, "app_state", None),
            user_scope=_scope_marker(api_key),
            request_id=request_id,
        )
        executor = ToolExecutor(registry)
        response = await executor.execute(tool_name, payload, ctx=ctx)
        latency_ms = (time.perf_counter() - start) * 1000

        # Server-side audit (separate from the executor's per-call event):
        # captures the wall-clock latency including FastAPI overhead.
        emit_mcp_event(
            event="mcp_request_complete",
            tool=tool_name,
            request_id=request_id,
            status=response.status.value,
            latency_ms=latency_ms,
            user_scope=_scope_marker(api_key),
            level="debug",
        )
        # Hash-chained audit entry — appended for success AND failure
        # (the executor never raises; failures are in-band). Best-effort:
        # an audit problem must never 500 the tool call.
        _append_audit(
            request,
            tool_name=tool_name,
            request_id=request_id,
            status=response.status.value,
            latency_ms=latency_ms,
            api_key=api_key,
        )
        return response


__all__ = ["router"]
