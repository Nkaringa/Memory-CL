from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from core.mcp.execution.tool_validator import (
    ToolValidationError,
    validate_tool_request,
)
from core.mcp.logevent import emit_mcp_event
from core.mcp.schemas import ToolErrorCode, ToolResponse, ToolStatus
from core.observability import get_tracer

_tracer = get_tracer("core.mcp.execution.tool_executor")


@dataclass(slots=True)
class ExecutionContext:
    """Per-request context handed to every tool.

    Holds the live `AppState` (so tools can talk to Phase 1-4) plus the
    deterministic request_id used in audit logs and OTEL spans.
    """

    request_id: str
    user_scope: str | None
    state: Any  # apps.api.state.AppState — typed Any to avoid circular import
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(cls, *, state: Any, user_scope: str | None = None,
            request_id: str | None = None) -> ExecutionContext:
        return cls(
            request_id=request_id or uuid.uuid4().hex[:16],
            user_scope=user_scope,
            state=state,
        )


@runtime_checkable
class Tool(Protocol):
    """Tools are pure orchestration wrappers around Phase 2-4 systems.

    `request` is typed `Any` at the Protocol boundary on purpose:
    concrete tools narrow it to their own request schema (which the
    executor guarantees via `validate_tool_request` before dispatch),
    and a `BaseModel` parameter type would make every narrowing tool
    fail the Protocol's contravariance check.
    """

    name: str

    # Read-only property (not a mutable attribute) so concrete tools can
    # declare a NARROWER schema class without tripping invariance.
    @property
    def request_schema(self) -> type[BaseModel]: ...

    async def execute(
        self, request: Any, ctx: ExecutionContext
    ) -> dict[str, Any]: ...


# Convenience type for the registry's name → tool map.
_ToolFactory = Callable[[], Tool]


class ToolRegistry:
    """Process-wide registry mapping tool names to instances.

    The registry is deliberately mutable so tests can register fakes;
    in production the registry is built once at server startup and not
    touched again.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not getattr(tool, "name", None):
            raise ValueError("Tool must define a non-empty `name`")
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[n] for n in self.names()]


class ToolExecutor:
    """Validate → invoke → wrap result.

    Failure handling per spec: on any exception we return a structured
    `ToolResponse` with status=FAILED. We never re-raise to the server,
    which would crash the connection. Each path emits the spec-mandated
    `mcp_tool_call` event.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        tool_name: str,
        payload: dict[str, Any],
        *,
        ctx: ExecutionContext,
    ) -> ToolResponse:
        start = time.perf_counter()
        with _tracer.start_as_current_span("mcp.tool.execution") as span:
            span.set_attribute("tool", tool_name)
            span.set_attribute("request_id", ctx.request_id)
            if ctx.user_scope:
                span.set_attribute("user_scope", ctx.user_scope)

            tool = self._registry.get(tool_name)
            if tool is None:
                return self._fail(
                    tool_name, ctx, ToolErrorCode.UNKNOWN_TOOL,
                    f"tool '{tool_name}' is not registered",
                    started_at=start,
                )

            try:
                with _tracer.start_as_current_span("mcp.tool.validation"):
                    request = validate_tool_request(
                        tool_name, tool.request_schema, payload
                    )
            except ToolValidationError as ve:
                return self._fail(
                    tool_name, ctx, ToolErrorCode.VALIDATION,
                    str(ve), started_at=start, errors=ve.errors,
                )

            try:
                data = await tool.execute(request, ctx)
            except Exception as exc:
                return self._fail(
                    tool_name, ctx, ToolErrorCode.BACKEND,
                    f"{type(exc).__name__}: {exc}",
                    started_at=start,
                )

            elapsed = (time.perf_counter() - start) * 1000
            response = ToolResponse(
                tool=tool_name,
                request_id=ctx.request_id,
                status=ToolStatus.SUCCESS,
                data=data,
                latency_ms=elapsed,
            )
            emit_mcp_event(
                event="mcp_tool_call",
                tool=tool_name,
                request_id=ctx.request_id,
                status="success",
                latency_ms=elapsed,
                user_scope=ctx.user_scope,
                level="info",
            )
            return response

    @staticmethod
    def _fail(
        tool_name: str,
        ctx: ExecutionContext,
        code: ToolErrorCode,
        message: str,
        *,
        started_at: float,
        errors: list[dict[str, Any]] | None = None,
    ) -> ToolResponse:
        elapsed = (time.perf_counter() - started_at) * 1000
        emit_mcp_event(
            event="mcp_tool_call",
            tool=tool_name,
            request_id=ctx.request_id,
            status="failed",
            latency_ms=elapsed,
            user_scope=ctx.user_scope,
            level="warning",
            error_code=code.value,
            error=message,
        )
        data: dict[str, Any] = {}
        if errors:
            data["errors"] = errors
        return ToolResponse(
            tool=tool_name,
            request_id=ctx.request_id,
            status=ToolStatus.FAILED,
            data=data,
            error=message,
            error_code=code,
            latency_ms=elapsed,
        )


__all__ = [
    "ExecutionContext",
    "Tool",
    "ToolExecutor",
    "ToolRegistry",
    "_ToolFactory",
]
