from __future__ import annotations

from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
)

from core.logging import get_logger

_log = get_logger(__name__)
_state: dict[str, Any] = {"started": False}


def start_observability(
    *,
    enabled: bool,
    service_name: str,
    otlp_endpoint: str | None,
) -> None:
    """Initialize tracer + meter providers exactly once.

    If `otlp_endpoint` is set we wire OTLP exporters; otherwise we fall back
    to console exporters so traces are never silently dropped during dev.
    """
    if _state["started"]:
        return
    if not enabled:
        _log.info("observability_disabled")
        _state["started"] = True
        return

    resource = Resource.create({"service.name": service_name})

    # ---- Tracing ----
    tracer_provider = TracerProvider(resource=resource)
    span_exporter: Any
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    else:
        span_exporter = ConsoleSpanExporter()
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    # ---- Metrics ----
    metric_exporter: Any
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )

        metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
    else:
        metric_exporter = ConsoleMetricExporter()
    reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=60_000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)

    _state["started"] = True
    _log.info("observability_started", service=service_name, otlp=bool(otlp_endpoint))


def shutdown_observability() -> None:
    if not _state["started"]:
        return
    try:
        provider = trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
        meter_provider = metrics.get_meter_provider()
        m_shutdown = getattr(meter_provider, "shutdown", None)
        if callable(m_shutdown):
            m_shutdown()
    finally:
        _state["started"] = False


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    return metrics.get_meter(name)
