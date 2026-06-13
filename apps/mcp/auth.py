"""API-key auth for the MCP layer.

Behavior:
    * If `Settings.mcp_api_key` is None / empty, the dependency is
      a no-op (dev mode). Production deployments MUST set the key.
    * Otherwise we accept the key via either `X-API-Key: <key>` or
      `Authorization: Bearer <key>` and reject everything else with 401.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from core import get_settings


def _extract_api_key(x_api_key: str | None, authorization: str | None) -> str | None:
    """Return whichever header bore a key, normalized to a bare string."""
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return None


def _resolve_expected_key(request: Request) -> str | None:
    """The expected MCP key, resolved Postgres-over-env.

    Prefers `app.state.runtime_config` (the onboarding runtime config:
    Postgres value if set, else env) so a key generated/rotated at
    runtime takes effect WITHOUT a restart. When no RuntimeConfig is
    attached (test apps, or any surface mounted outside the API
    lifespan), falls back to the env `Settings.mcp_api_key` — the EXACT
    pre-onboarding behavior, so existing deployments and tests are
    unaffected.
    """
    runtime = getattr(request.app.state, "runtime_config", None)
    if runtime is not None:
        key: str | None = runtime.mcp_api_key()
        return key
    settings = get_settings()
    expected = settings.mcp_api_key
    if expected is None or not expected.get_secret_value().strip():
        return None
    return expected.get_secret_value()


def require_mcp_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    """Dependency that gates the MCP endpoints behind a shared secret.

    Returns the matched key (sans transport prefix) so downstream
    handlers can include it in audit metadata when desired. The bare
    presence of a key never leaks back to the response body.
    """
    expected = _resolve_expected_key(request)
    if expected is None:
        return None  # dev mode

    presented = _extract_api_key(x_api_key, authorization)
    if presented is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if presented != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return presented


ApiKeyDep = Annotated[str | None, Depends(require_mcp_api_key)]
