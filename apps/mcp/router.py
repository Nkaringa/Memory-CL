"""MCP router — REST surface mounted under /mcp.

Endpoints:
    GET  /mcp/tools                 — list registered tools
    POST /mcp/tools/{tool_name}     — invoke a tool

Auth is enforced on the POST path. The GET path is informational and
intentionally cheap so an agent can discover the surface without
authenticating; it returns only static metadata (no data leakage).
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from apps.mcp.auth import ApiKeyDep
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
            user_scope=api_key,  # opaque scope marker for audit
            request_id=request_id,
        )
        executor = ToolExecutor(registry)
        response = await executor.execute(tool_name, payload, ctx=ctx)

        # Server-side audit (separate from the executor's per-call event):
        # captures the wall-clock latency including FastAPI overhead.
        emit_mcp_event(
            event="mcp_request_complete",
            tool=tool_name,
            request_id=request_id,
            status=response.status.value,
            latency_ms=(time.perf_counter() - start) * 1000,
            user_scope=api_key,
            level="debug",
        )
        return response


__all__ = ["router"]
