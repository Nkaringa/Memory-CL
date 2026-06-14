from __future__ import annotations

import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status

from apps.api.dependencies import (
    MembershipRepoDep,
    SessionCacheDep,
    SessionRepoDep,
    UserRepoDep,
)
from apps.mcp.auth import resolve_presented_key
from core.auth import Principal
from core.config import get_settings
from storage.org_repo import DEFAULT_ORG_ID

COOKIE_NAME = "memcl_session"


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def set_session_cookie(response: Response, raw: str, ttl_seconds: int) -> None:
    secure = get_settings().environment == "production"
    response.set_cookie(
        COOKIE_NAME,
        raw,
        max_age=ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    secure = get_settings().environment == "production"
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=secure)


async def get_principal(
    request: Request,
    session_repo: SessionRepoDep,
    membership_repo: MembershipRepoDep,
    user_repo: UserRepoDep,
    session_cache: SessionCacheDep,
) -> Principal:
    """Resolve the caller's identity without raising.

    Priority:
    1. Valid session cookie → user Principal with org roles.
    2. Accepted MCP API key (X-API-Key / Bearer) → agent Principal.
    3. Neither → anonymous Principal.
    """
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        sid = hash_session_token(raw)
        if session_cache.is_valid(sid):
            sess = await session_repo.get_active(sid)
            if sess is not None:
                m = await membership_repo.get_membership(
                    user_id=sess.user_id, org_id=sess.active_org_id
                )
                roles: tuple[str, ...] = (m.role,) if m else ()
                return Principal(
                    kind="user",
                    user_id=sess.user_id,
                    org_id=sess.active_org_id,
                    email="",
                    roles=roles,
                    is_authenticated=True,
                )

    key = resolve_presented_key(request)
    if key is not None:
        return Principal.agent(org_id=DEFAULT_ORG_ID)

    return Principal.anonymous()


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def require_principal(principal: PrincipalDep) -> Principal:
    """Dependency that raises 401 when the caller is not authenticated."""
    if not principal.is_authenticated:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return principal


RequirePrincipalDep = Annotated[Principal, Depends(require_principal)]
