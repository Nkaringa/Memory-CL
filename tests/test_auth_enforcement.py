"""Integration tests: authenticated session satisfies the protected config surface.

Covers the Phase-1 auth enforcement rule: after an MCP key is configured
(so the surface is no longer bootstrap-open), a human with a valid session
cookie can call protected config mutations WITHOUT presenting an API key.

The test runs the full FastAPI app in lite (SQLite) mode so the real
SessionCache, user/session repos, and runtime config are all exercised.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "lite")
    monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path))
    from core.config import get_settings
    get_settings.cache_clear()
    from apps.api.main import app
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_session_user_satisfies_protected_config(client: AsyncClient) -> None:
    """A logged-in human (session cookie) passes the bootstrap-or-authed gate
    on a configured system — without an API key in the request."""
    # Configure a key so the surface is no longer bootstrap-open.
    gen = await client.post("/config/mcp-key/generate")
    assert gen.status_code == 200, gen.text
    mcp_key = gen.json()["api_key"]

    # Register the first user (bootstrap: open, auto-logs-in, gets session cookie).
    reg = await client.post("/auth/register", json={"email": "a@b.c", "password": "password123", "display_name": "A"})
    assert reg.status_code == 200, reg.text
    assert reg.json()["authenticated"] is True

    # The session cookie is now present in the client (httpx persists cookies).
    # A protected config mutation WITHOUT an API key should now succeed.
    r = await client.post("/config/openai-key", json={"api_key": "sk-test"})
    assert r.status_code == 200, f"Expected 200 but got {r.status_code}: {r.text}"


@pytest.mark.anyio
async def test_anonymous_still_rejected_when_configured(client: AsyncClient) -> None:
    """An anonymous caller (no session, no API key) must still get 401 on a
    configured system — the additive session path must not weaken this."""
    gen = await client.post("/config/mcp-key/generate")
    assert gen.status_code == 200, gen.text

    # No session established, no key header → must be rejected.
    r = await client.post("/config/openai-key", json={"api_key": "sk-test"})
    assert r.status_code == 401, f"Expected 401 but got {r.status_code}: {r.text}"


@pytest.mark.anyio
async def test_api_key_still_works_when_configured(client: AsyncClient) -> None:
    """The existing API-key path must still work after the session path is added."""
    gen = await client.post("/config/mcp-key/generate")
    assert gen.status_code == 200, gen.text
    mcp_key = gen.json()["api_key"]

    r = await client.post("/config/openai-key", json={"api_key": "sk-test"}, headers={"X-API-Key": mcp_key})
    assert r.status_code == 200, f"Expected 200 but got {r.status_code}: {r.text}"


@pytest.mark.anyio
async def test_bootstrap_open_before_any_key(client: AsyncClient) -> None:
    """Before a key is configured the surface is open to anyone — unchanged."""
    r = await client.post("/config/openai-key", json={"api_key": "sk-test"})
    assert r.status_code == 200, f"Expected 200 but got {r.status_code}: {r.text}"
