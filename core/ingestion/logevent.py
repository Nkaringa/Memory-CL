from __future__ import annotations

from typing import Literal

from core.logging import get_logger

_log = get_logger("phase_2")

LogStatus = Literal["success", "failed", "partial"]


def emit_phase2_event(
    *,
    event: str,
    operation: str,
    status: LogStatus,
    duration_ms: float,
    unit_id: str | None = None,
    file_path: str | None = None,
    content_hash: str | None = None,
    level: Literal["debug", "info", "warning", "error"] = "info",
    **extra: object,
) -> None:
    """Emit a Phase-2 structured log line.

    Schema is fixed by the Phase-2 spec — every record carries:
    `event`, `phase=phase_2`, `unit_id`, `file_path`, `operation`,
    `status`, `duration_ms`, `content_hash`. Use `extra` for additional
    fields; they pass through structlog without overriding the spec keys.
    """
    payload: dict[str, object] = {
        "phase": "phase_2",
        "operation": operation,
        "status": status,
        "duration_ms": round(duration_ms, 3),
        "unit_id": unit_id or "",
        "file_path": file_path or "",
        "content_hash": content_hash or "",
        **extra,
    }
    log_method = getattr(_log, level)
    log_method(event, **payload)
