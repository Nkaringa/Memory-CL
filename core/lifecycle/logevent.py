from __future__ import annotations

from typing import Literal

from core.logging import get_logger

_log = get_logger("phase_6")

LifecycleOperation = Literal["decay", "promote", "compact", "refresh", "scan"]
LogStatus = Literal["success", "failed"]


def emit_phase6_event(
    *,
    event: str,
    entity_id: str,
    operation: LifecycleOperation,
    relevance_score: float,
    status: LogStatus,
    level: Literal["debug", "info", "warning", "error"] = "info",
    **extra: object,
) -> None:
    """Emit the spec-mandated `memory_evolution` event.

    Schema fields fixed by Phase-6: `event`, `phase=phase_6`, `entity_id`,
    `operation`, `relevance_score`, `status`. Extra context flows through
    as additional fields without overriding the spec keys.
    """
    payload: dict[str, object] = {
        "phase": "phase_6",
        "entity_id": entity_id,
        "operation": operation,
        "relevance_score": round(relevance_score, 6),
        "status": status,
        **extra,
    }
    log_method = getattr(_log, level)
    log_method(event, **payload)
