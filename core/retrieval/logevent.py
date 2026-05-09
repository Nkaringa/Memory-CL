from __future__ import annotations

from typing import Literal

from core.logging import get_logger

_log = get_logger("phase_4")

LogStatus = Literal["success", "failed", "degraded", "partial"]


def emit_phase4_event(
    *,
    event: str,
    operation: Literal[
        "plan", "graph_search", "vector_search", "metadata_query",
        "rank", "assemble", "retrieve",
    ],
    status: LogStatus,
    latency_ms: float,
    query_id: str | None = None,
    repo_id: str | None = None,
    level: Literal["debug", "info", "warning", "error"] = "info",
    **extra: object,
) -> None:
    """Phase-4 structured log emit.

    Schema fields fixed by spec: `event`, `phase=phase_4`, `query_id`,
    `repo_id`, `operation`, `status`, `latency_ms`. Channel hit counts
    and other metrics flow through `extra` and are passed through to
    structlog without overriding the spec keys.
    """
    payload: dict[str, object] = {
        "phase": "phase_4",
        "operation": operation,
        "status": status,
        "latency_ms": round(latency_ms, 3),
        "query_id": query_id or "",
        "repo_id": repo_id or "",
        **extra,
    }
    log_method = getattr(_log, level)
    log_method(event, **payload)
