from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request, Response, status

from apps.api.dependencies import AppStateDep
from schemas import (
    ComponentHealth,
    DependencyCheck,
    DependencyKind,
    DependencyReport,
    HealthStatus,
    LivenessResponse,
    ReadinessResponse,
)
from storage.base import StorageClient, StorageHealth

router = APIRouter(prefix="/health", tags=["health"])


# ---------------------------------------------------------------------------
# /health/live — liveness probe
# ---------------------------------------------------------------------------
@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Liveness probe — true iff the process is up.

    Intentionally does NOT touch storage. Use /health/ready for that.
    Kubernetes / orchestrators read this every few seconds; it must
    never block on an external dependency.
    """
    return LivenessResponse()


# ---------------------------------------------------------------------------
# /health/ready — readiness probe (storage + control-plane)
# ---------------------------------------------------------------------------
def _aggregate(components: list[ComponentHealth]) -> HealthStatus:
    if not components:
        return HealthStatus.OK
    if all(c.status == HealthStatus.OK for c in components):
        return HealthStatus.OK
    if all(c.status == HealthStatus.DOWN for c in components):
        return HealthStatus.DOWN
    return HealthStatus.DEGRADED


def _to_component(h: StorageHealth) -> ComponentHealth:
    return ComponentHealth(
        name=h.name,
        status=HealthStatus.OK if h.ok else HealthStatus.DOWN,
        latency_ms=round(h.latency_ms, 3),
        error=h.error,
    )


def _resolve_mcp_registry(request: Request) -> Any:
    """Pull the MCP registry off ``app.state``.

    The registry is attached during ``lifespan`` startup (see
    ``apps/api/lifespan.py``). It lives on the FastAPI app's state,
    NOT on the ``AppState`` dataclass — so we read it through the
    Request rather than the ``AppStateDep``.
    """
    return getattr(request.app.state, "mcp_registry", None)


def _mcp_registry_component(registry: Any) -> ComponentHealth:
    """Validate the MCP registry as a readiness gate.

    A live process with no MCP tools registered means the agent surface
    is broken even though the storage backends are fine — operators
    should see READY only when the registry is hot.
    """
    started = time.perf_counter()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    if registry is None:
        return ComponentHealth(
            name="mcp_registry",
            status=HealthStatus.DOWN,
            latency_ms=elapsed_ms,
            error="registry not attached to app state",
        )
    try:
        names = list(registry.names())
    except Exception as exc:  # pragma: no cover — defensive
        return ComponentHealth(
            name="mcp_registry",
            status=HealthStatus.DOWN,
            latency_ms=elapsed_ms,
            error=f"registry.names() failed: {exc!r}",
        )
    if len(names) < 7:
        return ComponentHealth(
            name="mcp_registry",
            status=HealthStatus.DEGRADED,
            latency_ms=elapsed_ms,
            error=f"only {len(names)} tools registered (expected ≥7)",
        )
    return ComponentHealth(
        name="mcp_registry",
        status=HealthStatus.OK,
        latency_ms=elapsed_ms,
        error=None,
    )


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(
    request: Request, state: AppStateDep, response: Response,
) -> ReadinessResponse:
    """Readiness probe — pings every backend in parallel.

    Returns 200 when all components are OK, 503 otherwise. Components are
    sorted by name for deterministic output. Phase 10 expansion: the
    MCP registry is now also a readiness gate, since a live process
    with zero tools is functionally not ready to serve agents.
    """
    clients: tuple[StorageClient, ...] = (state.postgres, state.qdrant, state.neo4j, state.redis)
    results = await asyncio.gather(*(c.ping() for c in clients))

    components = [_to_component(r) for r in results]
    components.append(_mcp_registry_component(_resolve_mcp_registry(request)))
    components.sort(key=lambda c: c.name)

    overall = _aggregate(components)

    if overall != HealthStatus.OK:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(status=overall, components=components)


# ---------------------------------------------------------------------------
# /health/dependencies — deep dependency inspection
# ---------------------------------------------------------------------------
async def _storage_check(client: StorageClient, kind: DependencyKind) -> DependencyCheck:
    health = await client.ping()
    return DependencyCheck(
        name=health.name,
        kind=kind,
        status=HealthStatus.OK if health.ok else HealthStatus.DOWN,
        required=True,
        latency_ms=round(health.latency_ms, 3),
        detail=None,
        error=health.error,
    )


def _mcp_check(registry: Any) -> DependencyCheck:
    """Same shape as ``_mcp_registry_component`` but in ``DependencyCheck`` form."""
    started = time.perf_counter()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    if registry is None:
        return DependencyCheck(
            name="mcp_registry",
            kind=DependencyKind.CONTROL,
            status=HealthStatus.DOWN,
            required=True,
            latency_ms=elapsed_ms,
            detail="registry not attached to app state",
            error="missing app.state.mcp_registry",
        )
    try:
        names = list(registry.names())
    except Exception as exc:
        return DependencyCheck(
            name="mcp_registry",
            kind=DependencyKind.CONTROL,
            status=HealthStatus.DOWN,
            required=True,
            latency_ms=elapsed_ms,
            error=f"registry.names() failed: {exc!r}",
        )
    if len(names) < 7:
        return DependencyCheck(
            name="mcp_registry",
            kind=DependencyKind.CONTROL,
            status=HealthStatus.DEGRADED,
            required=True,
            latency_ms=elapsed_ms,
            detail=f"{len(names)} tools registered",
            error=f"expected ≥7, found {len(names)}",
        )
    return DependencyCheck(
        name="mcp_registry",
        kind=DependencyKind.CONTROL,
        status=HealthStatus.OK,
        required=True,
        latency_ms=elapsed_ms,
        detail=f"{len(names)} tools registered",
    )


def _audit_check() -> DependencyCheck:
    started = time.perf_counter()
    try:
        from core.governance import AuditLogger
        intact = AuditLogger().verify()
    except Exception as exc:
        return DependencyCheck(
            name="audit_chain",
            kind=DependencyKind.GOVERNANCE,
            status=HealthStatus.DOWN,
            required=False,  # audit corruption shouldn't 503 the API
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            error=f"audit verify raised: {exc!r}",
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return DependencyCheck(
        name="audit_chain",
        kind=DependencyKind.GOVERNANCE,
        status=HealthStatus.OK if intact else HealthStatus.DEGRADED,
        required=False,
        latency_ms=elapsed_ms,
        detail="hash-chain intact" if intact else None,
        error=None if intact else "chain verification reported drift",
    )


def _aggregate_deps(checks: list[DependencyCheck]) -> HealthStatus:
    """Required checks dictate the gate; non-required ones only flag DEGRADED."""
    required = [c for c in checks if c.required]
    if required and any(c.status == HealthStatus.DOWN for c in required):
        if all(c.status == HealthStatus.DOWN for c in required):
            return HealthStatus.DOWN
        return HealthStatus.DEGRADED
    if required and not all(c.status == HealthStatus.OK for c in required):
        return HealthStatus.DEGRADED
    if any(c.status != HealthStatus.OK for c in checks):
        return HealthStatus.DEGRADED
    return HealthStatus.OK


@router.get("/dependencies", response_model=DependencyReport)
async def dependencies(
    request: Request, state: AppStateDep, response: Response,
) -> DependencyReport:
    """Deep dependency report — Phase-10 expansion of /health/ready.

    Surfaces:
        - storage probes (postgres, qdrant, neo4j, redis)
        - control-plane probes (MCP registry presence + arity)
        - governance probes (audit chain integrity — non-blocking)

    Returns 503 if any required check is DOWN, 200 otherwise. The
    output is sorted by name within kind so two identical runs produce
    byte-stable JSON for diffing.
    """
    storage_checks = await asyncio.gather(
        _storage_check(state.postgres, DependencyKind.STORAGE),
        _storage_check(state.qdrant, DependencyKind.STORAGE),
        _storage_check(state.neo4j, DependencyKind.STORAGE),
        _storage_check(state.redis, DependencyKind.STORAGE),
    )

    checks: list[DependencyCheck] = list(storage_checks)
    checks.append(_mcp_check(_resolve_mcp_registry(request)))
    checks.append(_audit_check())

    # Stable ordering: kind first (storage < control < governance is the
    # operational reading order), then name within kind.
    kind_order = {
        DependencyKind.STORAGE: 0,
        DependencyKind.CONTROL: 1,
        DependencyKind.GOVERNANCE: 2,
    }
    checks.sort(key=lambda c: (kind_order[c.kind], c.name))

    overall = _aggregate_deps(checks)
    # Safe-mode controller, like the MCP registry, lives on the FastAPI
    # app state — read it from the Request, NOT from the AppState dataclass.
    safe_mode = getattr(request.app.state, "safe_mode", None)
    safe_status = safe_mode.status if safe_mode is not None else None

    if overall == HealthStatus.DOWN or (
        overall == HealthStatus.DEGRADED
        and any(c.required and c.status == HealthStatus.DOWN for c in checks)
    ):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return DependencyReport(
        status=overall,
        checks=checks,
        safe_mode=bool(safe_status.enabled) if safe_status else False,
        safe_mode_reason=(safe_status.reason if safe_status and safe_status.enabled else None),
    )
