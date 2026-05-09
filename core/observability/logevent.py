from __future__ import annotations

from typing import Literal

from core.logging import get_logger

_log = get_logger("phase_7")

ScaleMetric = Literal["ingestion", "retrieval", "mcp", "graph", "vector"]
ScaleStatus = Literal["ok", "degraded", "failed"]


def emit_phase7_event(
    *,
    event: str,
    metric: ScaleMetric,
    latency_ms: float,
    throughput: float,
    shard_id: str | None = None,
    status: ScaleStatus = "ok",
    level: Literal["debug", "info", "warning", "error"] = "info",
    **extra: object,
) -> None:
    """Emit the spec-mandated `system_scale_event`.

    Schema fields fixed by Phase-7: `event`, `phase=phase_7`, `metric`,
    `latency_ms`, `throughput`, `shard_id`, `status`. Extra context
    flows through as additional structlog kwargs without overriding
    the spec keys.
    """
    payload: dict[str, object] = {
        "phase": "phase_7",
        "metric": metric,
        "latency_ms": round(latency_ms, 3),
        "throughput": round(throughput, 3),
        "shard_id": shard_id or "",
        "status": status,
        **extra,
    }
    log_method = getattr(_log, level)
    log_method(event, **payload)
