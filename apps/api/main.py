from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.middleware.sessions import SessionMiddleware

from apps.api.lifespan import lifespan
from apps.api.middleware import RequestContextMiddleware
from apps.api.routers import (
    audit,
    config,
    freshness,
    health,
    ingest,
    repos,
    retrieve,
    snapshot,
    status,
    webhooks,
)
from apps.api.routers import auth as auth_router
from apps.api.routers import oauth as oauth_router
from apps.mcp import mcp_router
from core import get_logger, get_settings

_log = get_logger(__name__)


def create_app() -> FastAPI:
    """Application factory.

    Use this in tests with custom settings overrides; production runs the
    module-level `app` below via uvicorn.
    """
    settings = get_settings()

    app = FastAPI(
        title="Memory-CL",
        version="0.1.0",
        description="AI-native project memory system",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Phase-10: every request gets a stable X-Request-ID echoed back to
    # the caller and bound to all structlog events + the OTEL span.
    app.add_middleware(RequestContextMiddleware)

    # OAuth state cookie (Phase 2 federation). Starlette SessionMiddleware
    # signs and encrypts the session cookie with the configured secret.
    # Falls back to mcp_api_key value, then a dev constant — warns if prod.
    _oauth_secret = settings.oauth_state_secret
    if not _oauth_secret:
        _mcp = settings.mcp_api_key
        _oauth_secret = _mcp.get_secret_value() if _mcp is not None else ""
    if not _oauth_secret:
        _oauth_secret = "memcl-oauth-dev-secret"
        if settings.environment == "production":
            _log.warning("oauth_state_secret_not_set", recommendation="Set OAUTH_STATE_SECRET in production to a strong random value")
    app.add_middleware(SessionMiddleware, secret_key=_oauth_secret, session_cookie="memcl_oauth", max_age=300, same_site="lax", https_only=(settings.environment == "production"))

    # Phase 1-5 surfaces.
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(retrieve.router)
    app.include_router(mcp_router)
    # Phase 9 additive surfaces.
    app.include_router(snapshot.router)
    app.include_router(audit.router)
    app.include_router(status.router)
    app.include_router(repos.router)
    app.include_router(freshness.router)
    app.include_router(webhooks.router)
    # Onboarding Phase 1: runtime config + key management.
    app.include_router(config.router)
    # Identity / Auth endpoints.
    app.include_router(auth_router.router)
    # OAuth public + flow endpoints (Phase 2 federation).
    app.include_router(oauth_router.router)

    if settings.ui_enabled:
        ui_dir = Path(__file__).resolve().parent.parent / "ui" / "static"
        if ui_dir.exists():
            app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

    if settings.otel_enabled:
        FastAPIInstrumentor.instrument_app(app)

    return app


app: FastAPI = create_app()
