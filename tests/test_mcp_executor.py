from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ConfigDict, Field

from core.mcp.execution import (
    ExecutionContext,
    Tool,
    ToolExecutor,
    ToolRegistry,
)
from core.mcp.execution.tool_validator import (
    ToolValidationError,
    validate_tool_request,
)
from core.mcp.schemas import ToolErrorCode, ToolStatus


# ---- Fakes -----------------------------------------------------------------
class _DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)


class _DemoTool:
    name: str = "demo"
    request_schema = _DemoRequest

    async def execute(self, request: _DemoRequest, ctx: ExecutionContext) -> dict[str, Any]:
        return {"echo": request.name, "request_id": ctx.request_id}


class _BoomTool:
    name: str = "boom"
    request_schema = _DemoRequest

    async def execute(self, request: _DemoRequest, ctx: ExecutionContext) -> dict[str, Any]:
        raise RuntimeError("kaboom")


# ---- ToolValidator ---------------------------------------------------------
def test_validator_passes_valid_payload() -> None:
    out = validate_tool_request("demo", _DemoRequest, {"name": "x"})
    assert isinstance(out, _DemoRequest)
    assert out.name == "x"


def test_validator_raises_structured_error_on_invalid() -> None:
    with pytest.raises(ToolValidationError) as ei:
        validate_tool_request("demo", _DemoRequest, {"name": ""})
    assert ei.value.tool == "demo"
    assert isinstance(ei.value.errors, list)
    assert ei.value.errors and "loc" in ei.value.errors[0]


# ---- Registry --------------------------------------------------------------
def test_registry_register_and_lookup() -> None:
    r = ToolRegistry()
    r.register(_DemoTool())
    assert r.get("demo").name == "demo"
    assert r.get("missing") is None
    assert r.names() == ["demo"]


def test_registry_rejects_duplicate_names() -> None:
    r = ToolRegistry()
    r.register(_DemoTool())
    with pytest.raises(ValueError):
        r.register(_DemoTool())


def test_registry_rejects_nameless_tool() -> None:
    bad = MagicMock()
    bad.name = ""
    with pytest.raises(ValueError):
        ToolRegistry().register(bad)


# ---- ToolExecutor ----------------------------------------------------------
def _ctx() -> ExecutionContext:
    return ExecutionContext.new(state=object(), request_id="rid-test")


@pytest.mark.asyncio
async def test_executor_runs_tool_and_wraps_result() -> None:
    r = ToolRegistry()
    r.register(_DemoTool())
    resp = await ToolExecutor(r).execute("demo", {"name": "auth"}, ctx=_ctx())
    assert resp.status == ToolStatus.SUCCESS
    assert resp.tool == "demo"
    assert resp.request_id == "rid-test"
    assert resp.data["echo"] == "auth"
    assert resp.error is None
    assert resp.error_code is None
    assert resp.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_executor_unknown_tool_returns_error_response() -> None:
    resp = await ToolExecutor(ToolRegistry()).execute("missing", {}, ctx=_ctx())
    assert resp.status == ToolStatus.FAILED
    assert resp.error_code == ToolErrorCode.UNKNOWN_TOOL


@pytest.mark.asyncio
async def test_executor_validation_failure_returns_error_response() -> None:
    r = ToolRegistry()
    r.register(_DemoTool())
    resp = await ToolExecutor(r).execute("demo", {"name": ""}, ctx=_ctx())
    assert resp.status == ToolStatus.FAILED
    assert resp.error_code == ToolErrorCode.VALIDATION
    # Structured error payload exposed under data.errors.
    assert "errors" in resp.data
    assert isinstance(resp.data["errors"], list)


@pytest.mark.asyncio
async def test_executor_backend_failure_returns_error_response() -> None:
    r = ToolRegistry()
    r.register(_BoomTool())
    resp = await ToolExecutor(r).execute("boom", {"name": "x"}, ctx=_ctx())
    assert resp.status == ToolStatus.FAILED
    assert resp.error_code == ToolErrorCode.BACKEND
    assert "kaboom" in resp.error


@pytest.mark.asyncio
async def test_executor_never_raises() -> None:
    """Spec: 'do NOT crash MCP server'."""
    r = ToolRegistry()
    r.register(_BoomTool())
    # Three failure modes — none should raise.
    for payload in [{"name": "x"}, {}, {"name": ""}]:
        resp = await ToolExecutor(r).execute("boom", payload, ctx=_ctx())
        assert resp.status == ToolStatus.FAILED


def test_tool_protocol_recognises_concrete_tools() -> None:
    assert isinstance(_DemoTool(), Tool)
