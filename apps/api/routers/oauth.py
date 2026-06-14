"""OAuth public endpoints.

Phase-2 federation — Task 5 seeds this router with the public provider list
endpoint.  Task 8 adds the full /auth/oauth/* flow endpoints.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.responses import RedirectResponse

from apps.api.dependencies import (
    AuthProviderRepoDep,
    FederatedIdentityRepoDep,
    MembershipRepoDep,
    OAuthRegistryDep,
    SessionCacheDep,
    SessionRepoDep,
    UserRepoDep,
)
from apps.api.routers.auth import _create_session, provision_user
from storage.org_repo import DEFAULT_ORG_ID

router = APIRouter(tags=["oauth"])


@router.get("/auth/providers")
async def list_providers(registry: OAuthRegistryDep) -> dict:
    """Return the list of enabled OAuth/OIDC providers (no secrets)."""
    return {"providers": registry.enabled_public_list()}


@router.get("/auth/oauth/{provider_id}/start")
async def oauth_start(
    provider_id: str,
    request: Request,
    registry: OAuthRegistryDep,
) -> Response:
    """Redirect the user to the OAuth/OIDC provider's authorization endpoint."""
    client = registry.client_for(provider_id)
    if client is None:
        raise HTTPException(status_code=404, detail="provider not enabled")
    redirect_uri = str(request.url_for("oauth_callback", provider_id=provider_id))
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/auth/oauth/{provider_id}/callback", name="oauth_callback")
async def oauth_callback(
    provider_id: str,
    request: Request,
    registry: OAuthRegistryDep,
    federated_identity_repo: FederatedIdentityRepoDep,
    user_repo: UserRepoDep,
    membership_repo: MembershipRepoDep,
    session_repo: SessionRepoDep,
    session_cache: SessionCacheDep,
) -> Response:
    """Handle the OAuth/OIDC callback: exchange code, link identity, create session."""
    # Step 1: resolve client
    client = registry.client_for(provider_id)
    if client is None:
        raise HTTPException(status_code=404, detail="provider not enabled")

    # Step 2: provider type
    provider_type = registry.provider_type_for(provider_id)

    # Step 3: exchange code for token
    try:
        token = await client.authorize_access_token(request)
    except Exception:
        raise HTTPException(status_code=400, detail="oauth exchange failed")

    # Step 4: resolve (subject, verified_email, display_name)
    if provider_type == "github":
        u = (await client.get("user")).json()
        subject = str(u["id"])
        emails = (await client.get("user/emails")).json()
        # Prefer verified+primary, then any verified
        verified_email = next(
            (e["email"] for e in emails if e.get("verified") and e.get("primary")),
            None,
        ) or next(
            (e["email"] for e in emails if e.get("verified")),
            None,
        )
        display_name = u.get("name") or u.get("login") or verified_email
    else:
        # OIDC (google, microsoft, generic)
        info = token.get("userinfo") or {}
        subject = info.get("sub")
        if provider_type == "microsoft":
            # Entra ID emails are always verified; claim may be absent (default True)
            verified_email = info.get("email") if info.get("email_verified", True) else None
        else:
            # google / generic OIDC: require explicit email_verified=True
            verified_email = info.get("email") if info.get("email_verified") else None
        display_name = info.get("name") or verified_email

    if not subject:
        raise HTTPException(status_code=400, detail="no subject from provider")

    # Step 5: check for existing federated identity
    fid = await federated_identity_repo.get_by_subject(provider=provider_id, subject=subject)
    if fid is not None:
        # Straight login — no account creation needed
        user_id = fid.user_id
    else:
        # Step 6: require a verified email to link/create
        if not verified_email:
            raise HTTPException(status_code=400, detail="provider did not supply a verified email")
        verified_email = verified_email.lower()

        # Link by verified email (or create new user)
        existing = await user_repo.get_by_email(verified_email)
        if existing is not None:
            user_id = existing.user_id
        else:
            user_id, _role = await provision_user(
                email=verified_email,
                display_name=display_name or verified_email,
                user_repo=user_repo,
                membership_repo=membership_repo,
                password_hash=None,
            )

        # Bind the federated identity
        await federated_identity_repo.add(
            id=secrets.token_urlsafe(12),
            user_id=user_id,
            provider=provider_id,
            subject=subject,
            email=verified_email,
        )

    # Step 7+8: create session on the RedirectResponse itself so the
    # Set-Cookie header is carried with the 302 redirect.
    redirect = RedirectResponse("/", status_code=302)
    await _create_session(
        user_id=user_id,
        org_id=DEFAULT_ORG_ID,
        response=redirect,
        session_repo=session_repo,
        session_cache=session_cache,
    )
    return redirect
