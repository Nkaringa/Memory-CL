"""Auth endpoints: register, login, logout, me.

Bootstrap rule for /register:
- First user (count == 0) → open; granted owner role; auto-logged-in.
- Subsequent users → require the caller to be an authenticated owner or admin.
  Anonymous callers → 401. Authenticated but insufficient role → 403.
  (Invitations / self-service sign-up come later.)
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status

from apps.api.auth_deps import (
    COOKIE_NAME,
    PrincipalDep,
    clear_session_cookie,
    hash_session_token,
    new_session_token,
    set_session_cookie,
)
from apps.api.dependencies import (
    MembershipRepoDep,
    OrgRepoDep,
    SessionCacheDep,
    SessionRepoDep,
    UserRepoDep,
)
from core.auth import ROLE_ADMIN, ROLE_MEMBER, ROLE_OWNER, hash_password, verify_password
from core.config import get_settings
from schemas.auth import LoginRequest, MeResponse, RegisterRequest, UserView
from storage.org_repo import DEFAULT_ORG_ID

# Compute once at import time so the login path can equalise timing when the
# email is not found (prevents user-enumeration via response-time oracle).
_DUMMY_PW_HASH = hash_password("memcl-timing-equalizer")

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Provisioning helper (shared by /register and future OIDC callback)
# ---------------------------------------------------------------------------

async def provision_user(
    *,
    email: str,
    display_name: str,
    user_repo,            # UserRepository
    membership_repo,      # MembershipRepository
    password_hash: str | None = None,
) -> tuple[str, str]:
    """Create a user + default-org membership. First-ever user becomes owner.
    Returns (user_id, role). If password_hash is None, no local credential is set
    (used by federated/OIDC signups)."""
    is_bootstrap = await user_repo.count_users() == 0
    user_id = secrets.token_urlsafe(12)
    await user_repo.create_user(user_id=user_id, email=email, display_name=display_name)
    if password_hash is not None:
        await user_repo.set_password(user_id=user_id, password_hash=password_hash)
    role = ROLE_OWNER if is_bootstrap else ROLE_MEMBER
    membership_id = secrets.token_urlsafe(12)
    await membership_repo.add_member(membership_id=membership_id, user_id=user_id, org_id=DEFAULT_ORG_ID, role=role)
    return user_id, role


# ---------------------------------------------------------------------------
# Session creation helper
# ---------------------------------------------------------------------------

async def _create_session(
    *,
    user_id: str,
    org_id: str,
    response: Response,
    session_repo: SessionRepoDep,
    session_cache: SessionCacheDep,
) -> None:
    """Create a server-side session and set the session cookie."""
    raw = new_session_token()
    sid = hash_session_token(raw)
    ttl = get_settings().session_ttl_seconds
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    await session_repo.create_session(
        session_id=sid,
        user_id=user_id,
        active_org_id=org_id,
        csrf_token=new_session_token(),
        expires_at=expires,
    )
    session_cache.add(sid)
    set_session_cookie(response, raw, ttl)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=MeResponse)
async def register(
    body: RegisterRequest,
    response: Response,
    principal: PrincipalDep,
    user_repo: UserRepoDep,
    membership_repo: MembershipRepoDep,
    org_repo: OrgRepoDep,
    session_repo: SessionRepoDep,
    session_cache: SessionCacheDep,
) -> MeResponse:
    count = await user_repo.count_users()
    is_bootstrap = count == 0

    if not is_bootstrap:
        if not principal.is_authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required to add users",
            )
        if not (principal.has_role(ROLE_OWNER) or principal.has_role(ROLE_ADMIN)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only owners and admins can register new users",
            )

    # Reject duplicate email.
    existing = await user_repo.get_by_email(body.email)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    # Ensure default org exists (idempotent).
    await org_repo.ensure_default_org()

    user_id, role = await provision_user(
        email=body.email,
        display_name=body.display_name,
        user_repo=user_repo,
        membership_repo=membership_repo,
        password_hash=hash_password(body.password),
    )

    if is_bootstrap:
        await _create_session(
            user_id=user_id,
            org_id=DEFAULT_ORG_ID,
            response=response,
            session_repo=session_repo,
            session_cache=session_cache,
        )

    user_view = UserView(
        user_id=user_id,
        email=body.email,
        display_name=body.display_name,
        org_id=DEFAULT_ORG_ID,
        roles=[role],
    )
    # authenticated reflects whether THIS call established a session for the
    # newly created user (true only on first-user bootstrap); an admin
    # creating another user keeps their own session and is not re-logged-in.
    return MeResponse(authenticated=is_bootstrap, user=user_view)


@router.post("/login", response_model=MeResponse)
async def login(
    body: LoginRequest,
    response: Response,
    user_repo: UserRepoDep,
    membership_repo: MembershipRepoDep,
    session_repo: SessionRepoDep,
    session_cache: SessionCacheDep,
) -> MeResponse:
    user = await user_repo.get_by_email(body.email)
    if user is None:
        # Always run a dummy verify of equivalent cost so an attacker cannot
        # distinguish "no such email" from "wrong password" via timing.
        verify_password(body.password, _DUMMY_PW_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    h = await user_repo.get_password_hash(user.user_id)
    if not h:
        # No credential row stored — equalise timing before 401.
        verify_password(body.password, _DUMMY_PW_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not verify_password(body.password, h):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    org_id = DEFAULT_ORG_ID
    await _create_session(
        user_id=user.user_id,
        org_id=org_id,
        response=response,
        session_repo=session_repo,
        session_cache=session_cache,
    )

    m = await membership_repo.get_membership(user_id=user.user_id, org_id=org_id)
    roles = [m.role] if m else []
    user_view = UserView(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        org_id=org_id,
        roles=roles,
    )
    return MeResponse(authenticated=True, user=user_view)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session_repo: SessionRepoDep,
    session_cache: SessionCacheDep,
) -> dict:
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        sid = hash_session_token(raw)
        await session_repo.revoke(sid)
        session_cache.invalidate(sid)
        clear_session_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(
    principal: PrincipalDep,
    user_repo: UserRepoDep,
) -> MeResponse:
    if not principal.is_authenticated:
        return MeResponse(authenticated=False)

    if principal.kind == "agent":
        user_view = UserView(
            user_id=principal.user_id,
            email="",
            display_name="agent",
            org_id=principal.org_id,
            roles=list(principal.roles),
        )
        return MeResponse(authenticated=True, user=user_view)

    u = await user_repo.get_user(principal.user_id)
    user_view = UserView(
        user_id=principal.user_id,
        email=u.email if u else "",
        display_name=u.display_name if u else "",
        org_id=principal.org_id,
        roles=list(principal.roles),
    )
    return MeResponse(authenticated=True, user=user_view)
