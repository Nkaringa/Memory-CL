"""Observability surface.

Phase-1 OTEL bootstrap (`start_observability`, `shutdown_observability`,
`get_tracer`, `get_meter`) lives in `_otel.py` and is re-exported
verbatim from this package — every existing import continues to work.

Phase-7 adds:
    * `latency_tracker.LatencyTracker`
    * `throughput_analyzer.ThroughputAnalyzer`
    * `system_health_monitor.SystemHealthMonitor`
"""

from core.observability._otel import (
    get_meter,
    get_tracer,
    shutdown_observability,
    start_observability,
)
from core.observability.latency_tracker import LatencySnapshot, LatencyTracker
from core.observability.system_health_monitor import (
    HealthSnapshot,
    HealthStatus,
    SystemHealthMonitor,
)
from core.observability.throughput_analyzer import (
    ThroughputAnalyzer,
    ThroughputSnapshot,
)

__all__ = [
    "HealthSnapshot",
    "HealthStatus",
    "LatencySnapshot",
    "LatencyTracker",
    "SystemHealthMonitor",
    "ThroughputAnalyzer",
    "ThroughputSnapshot",
    "get_meter",
    "get_tracer",
    "shutdown_observability",
    "start_observability",
]
