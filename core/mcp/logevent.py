from __future__ import annotations

from typing import Literal

from core.logging import get_logger

_log = get_logger("phase_5")


def emit_mcp_event(
    *,
    event: str,
    tool: str,
    request_id: str,
    status: Literal["success", "failed"],
    latency_ms: float,
    user_scope: str | None = None,
    level: Literal["debug", "info", "warning", "error"] = "info",
    **extra: object,
) -> None:
    """Emit the spec-mandated mcp_tool_call event.

    Schema is fixed by Phase-5 spec: `event`, `tool`, `phase=phase_5`,
    `request_id`, `status`, `latency_ms`, `user_scope`. `extra` flows
    through but cannot override the spec keys.
    """
    payload: dict[str, object] = {
        "phase": "phase_5",
        "tool": tool,
        "request_id": request_id,
        "status": status,
        "latency_ms": round(latency_ms, 3),
        "user_scope": user_scope,
        **extra,
    }
    log_method = getattr(_log, level)
    log_method(event, **payload)
