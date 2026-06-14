"""OAuth public endpoints.

Phase-2 federation — Task 5 seeds this router with the public provider list
endpoint.  Task 8 will add the full /auth/oauth/* flow endpoints here.
"""

from __future__ import annotations

from fastapi import APIRouter

from apps.api.dependencies import OAuthRegistryDep

router = APIRouter(tags=["oauth"])


@router.get("/auth/providers")
async def list_providers(registry: OAuthRegistryDep) -> dict:
    """Return the list of enabled OAuth/OIDC providers (no secrets)."""
    return {"providers": registry.enabled_public_list()}
