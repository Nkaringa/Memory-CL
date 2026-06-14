"""Unit tests for the get_principal dependency and resolve_presented_key helper.

These focus on the logic paths — cookie lookup, API key fallback, and
anonymous fallback — using lightweight mocks rather than the full app stack.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.api.auth_deps import get_principal, hash_session_token, new_session_token
from apps.mcp.auth import resolve_presented_key
from core.auth import Principal


# ---------------------------------------------------------------------------
# resolve_presented_key tests
# ---------------------------------------------------------------------------

class _FakeHeaders(dict):
    """Case-insensitive dict so `req.headers.get("X-API-Key")` works."""
    def get(self, key, default=None):
        return super().get(key.lower(), default)


def _make_request(headers: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.headers = _FakeHeaders({k.lower(): v for k, v in (headers or {}).items()})
    req.app.state = MagicMock()
    return req


def test_resolve_presented_key_no_key_presented_returns_none():
    """When no X-API-Key / Authorization header is present, returns None regardless of dev/prod mode."""
    req = _make_request()
    req.app.state.runtime_config = None
    with patch("apps.mcp.auth._resolve_expected_key", return_value=None), \
         patch("apps.mcp.auth.auth_is_configured", return_value=False):
        result = resolve_presented_key(req)
    assert result is None  # no credential presented → not an agent caller


def test_resolve_presented_key_dev_mode_key_presented_accepted():
    """In dev mode with a key presented, resolve returns the key (accepted)."""
    req = _make_request({"X-API-Key": "any-key"})
    req.app.state.runtime_config = None
    with patch("apps.mcp.auth._resolve_expected_key", return_value=None), \
         patch("apps.mcp.auth.auth_is_configured", return_value=False):
        result = resolve_presented_key(req)
    assert result == "any-key"


def test_resolve_presented_key_missing_key_returns_none():
    req = _make_request()
    with patch("apps.mcp.auth._resolve_expected_key", return_value="secret"), \
         patch("apps.mcp.auth.auth_is_configured", return_value=True), \
         patch("apps.mcp.auth.credential_accepted", return_value=False):
        result = resolve_presented_key(req)
    assert result is None


def test_resolve_presented_key_valid_key_returns_key():
    req = _make_request({"X-API-Key": "good-key"})
    with patch("apps.mcp.auth._resolve_expected_key", return_value="good-key"), \
         patch("apps.mcp.auth.auth_is_configured", return_value=True), \
         patch("apps.mcp.auth.credential_accepted", return_value=True):
        result = resolve_presented_key(req)
    assert result == "good-key"


# ---------------------------------------------------------------------------
# get_principal tests (async, using direct mocks)
# ---------------------------------------------------------------------------

def _make_deps(*, session_valid: bool = False, session_row=None, membership_row=None, api_key_accepted: bool | None = None):
    session_cache = MagicMock()
    session_cache.is_valid = MagicMock(return_value=session_valid)

    session_repo = AsyncMock()
    session_repo.get_active = AsyncMock(return_value=session_row)

    membership_repo = AsyncMock()
    membership_repo.get_membership = AsyncMock(return_value=membership_row)

    user_repo = AsyncMock()
    return session_cache, session_repo, membership_repo, user_repo


@pytest.mark.anyio
async def test_get_principal_anonymous_when_no_cookie_no_key():
    req = MagicMock()
    req.cookies = {}
    session_cache, session_repo, membership_repo, user_repo = _make_deps()

    with patch("apps.api.auth_deps.resolve_presented_key", return_value=None):
        p = await get_principal(req, session_repo, membership_repo, user_repo, session_cache)

    assert p.is_authenticated is False
    assert p.kind == "user"
    assert p.user_id == ""


@pytest.mark.anyio
async def test_get_principal_agent_when_api_key_accepted():
    req = MagicMock()
    req.cookies = {}
    session_cache, session_repo, membership_repo, user_repo = _make_deps()

    with patch("apps.api.auth_deps.resolve_presented_key", return_value="some-key"):
        p = await get_principal(req, session_repo, membership_repo, user_repo, session_cache)

    assert p.is_authenticated is True
    assert p.kind == "agent"
    assert p.user_id == "agent"


@pytest.mark.anyio
async def test_get_principal_user_when_valid_session():
    raw = new_session_token()
    sid = hash_session_token(raw)

    session_row = MagicMock()
    session_row.user_id = "user-123"
    session_row.active_org_id = "default"

    membership_row = MagicMock()
    membership_row.role = "owner"

    session_cache, session_repo, membership_repo, user_repo = _make_deps(
        session_valid=True, session_row=session_row, membership_row=membership_row
    )

    req = MagicMock()
    req.cookies = {"memcl_session": raw}

    p = await get_principal(req, session_repo, membership_repo, user_repo, session_cache)

    assert p.is_authenticated is True
    assert p.kind == "user"
    assert p.user_id == "user-123"
    assert "owner" in p.roles


@pytest.mark.anyio
async def test_get_principal_falls_back_when_session_cache_miss():
    raw = new_session_token()
    session_cache, session_repo, membership_repo, user_repo = _make_deps(session_valid=False)
    req = MagicMock()
    req.cookies = {"memcl_session": raw}

    with patch("apps.api.auth_deps.resolve_presented_key", return_value=None):
        p = await get_principal(req, session_repo, membership_repo, user_repo, session_cache)

    assert p.is_authenticated is False
