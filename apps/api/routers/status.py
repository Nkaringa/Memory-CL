"""System status surface — Phase-9 production observability summary."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from apps.api.dependencies import AppStateDep
from core import get_settings

router = APIRouter(prefix="/status", tags=["status"])


class FeatureFlagView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    enabled: bool


class BootStageView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    order: int
    status: str
    error: str = ""


class SafeModeView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    reason: str
    triggered_by: str
    # Phase-10 expansion — discrete mode label, "off" when enabled=False.
    mode: str = "off"


class StatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    environment: str
    safe_mode: SafeModeView
    feature_flags: list[FeatureFlagView]
    boot_overall_ok: bool
    boot_failed_stages: list[str]
    boot_degraded_stages: list[str]
    boot_stages: list[BootStageView]
    mcp_tool_count: int
    schema_version: str
    embeddings_enabled: bool


@router.get("", response_model=StatusResponse)
async def status_summary(
    request: Request, state: AppStateDep,
) -> StatusResponse:
    """One-shot view of the service's production posture.

    The Phase-9 controllers (``safe_mode``, ``feature_flags``,
    ``boot_outcome``, ``mcp_registry``, ``audit_logger``) live on the
    FastAPI ``app.state`` bag — NOT on the ``AppState`` dataclass.
    The earlier version of this handler read them off ``state`` and
    silently received ``None`` for everything in production, even
    though the controllers were correctly attached during ``lifespan``.
    """
    settings = get_settings()
    app_state = request.app.state
    sm = getattr(app_state, "safe_mode", None)
    safe_mode = sm.status if sm is not None else None
    flags = getattr(app_state, "feature_flags", None)
    boot = getattr(app_state, "boot_outcome", None)
    registry = getattr(app_state, "mcp_registry", None)
    _ = state  # AppState (storage clients) — not used here, but kept
               # in the signature so the dependency wiring stays uniform

    from schemas.base import SCHEMA_VERSION
    return StatusResponse(
        service=settings.service_label,
        environment=settings.environment,
        safe_mode=SafeModeView(
            enabled=safe_mode.enabled if safe_mode else False,
            reason=safe_mode.reason if safe_mode else "",
            triggered_by=safe_mode.triggered_by if safe_mode else "",
            mode=getattr(safe_mode, "mode", "off") if safe_mode else "off",
        ),
        feature_flags=[
            FeatureFlagView(
                name=f.name, description=f.description, enabled=f.enabled,
            )
            for f in (flags.all() if flags else [])
        ],
        boot_overall_ok=boot.overall_ok if boot else False,
        boot_failed_stages=list(boot.failed_stages) if boot else [],
        boot_degraded_stages=list(boot.degraded_stages) if boot else [],
        boot_stages=[
            BootStageView(
                name=r.name, order=r.order,
                status=r.status.value, error=r.error,
            )
            for r in (boot.results if boot else ())
        ],
        mcp_tool_count=len(registry.names()) if registry else 0,
        schema_version=SCHEMA_VERSION,
        embeddings_enabled=settings.embeddings_enabled,
    )
