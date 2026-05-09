"""Standalone MCP app builder (used in MCP-only deployments).

The default deployment mounts the MCP router on the main API app via
`apps.mcp.mcp_router`. This module is provided so a future deployment
that wants to run MCP on a separate ASGI service can do so without
duplicating wiring.
"""

from __future__ import annotations

from fastapi import FastAPI

from apps.mcp.registry import build_default_registry
from apps.mcp.router import router as mcp_router


def build_mcp_app() -> FastAPI:
    """Construct a minimal FastAPI app exposing only the MCP surface.

    Callers MUST attach `app.state.app_state` (an `AppState`) before
    handling requests — the tool executor reads it for the live storage
    clients. The router does NOT initialise the registry; that is the
    job of the surrounding lifespan, exactly as the main API does.
    """
    app = FastAPI(
        title="Memory-CL MCP",
        version="0.1.0",
        description="MCP tool surface for the AI Project Memory System",
    )
    app.include_router(mcp_router)
    app.state.mcp_registry = build_default_registry()
    return app
