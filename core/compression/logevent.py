from __future__ import annotations

from typing import Literal

from core.logging import get_logger

_log = get_logger("phase_3")

LogStatus = Literal["success", "failed", "degraded", "partial"]


def emit_phase3_event(
    *,
    event: str,
    operation: Literal["compress", "summarize", "embed", "encode", "chunk"],
    status: LogStatus,
    duration_ms: float,
    unit_id: str | None = None,
    token_reduction_ratio: float | None = None,
    level: Literal["debug", "info", "warning", "error"] = "info",
    **extra: object,
) -> None:
    """Emit a Phase-3 structured log line.

    Schema is fixed by the Phase-3 spec — every record carries:
    `event`, `phase=phase_3`, `unit_id`, `operation`, `status`,
    `token_reduction_ratio`, `duration_ms`. Use `extra` for additional
    fields; they pass through structlog without overriding spec keys.
    """
    payload: dict[str, object] = {
        "phase": "phase_3",
        "operation": operation,
        "status": status,
        "duration_ms": round(duration_ms, 3),
        "unit_id": unit_id or "",
        "token_reduction_ratio": (
            round(token_reduction_ratio, 6)
            if token_reduction_ratio is not None
            else 0.0
        ),
        **extra,
    }
    log_method = getattr(_log, level)
    log_method(event, **payload)
