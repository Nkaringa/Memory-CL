from __future__ import annotations

import hashlib
import secrets

from fastapi import Response

from core.config import get_settings

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
    response.delete_cookie(COOKIE_NAME, path="/")
