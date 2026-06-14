"""Integration tests for the OAuth start + callback flow.

Uses the full ASGI lifespan (lite mode) so real SQLite-backed repos are
exercised. Fake OAuth clients replace authlib remote apps via
install_fake_provider().
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def client_and_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "lite")
    monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path))
    from core.config import get_settings

    get_settings.cache_clear()
    from apps.api.main import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            yield c, app
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _logout(c: AsyncClient) -> None:
    r = await c.post("/auth/logout")
    assert r.status_code == 200


async def _register(c: AsyncClient, email: str = "owner@x.c", password: str = "password123", display_name: str = "Owner") -> None:
    r = await c.post("/auth/register", json={"email": email, "password": password, "display_name": display_name})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_start_redirects_for_enabled_provider(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider

    await install_fake_provider(
        app,
        provider_id="p-google",
        provider_type="google",
        userinfo={"sub": "g-1", "email": "test@x.c", "email_verified": True, "name": "Test"},
    )

    r = await c.get("/auth/oauth/p-google/start", follow_redirects=False)
    assert r.status_code in (302, 307), f"Expected redirect, got {r.status_code}: {r.text}"


@pytest.mark.anyio
async def test_callback_creates_first_user_as_owner(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider

    await install_fake_provider(
        app,
        provider_id="p-google",
        provider_type="google",
        userinfo={"sub": "g-1", "email": "new@x.c", "email_verified": True, "name": "New"},
    )

    r = await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code == 302, f"Expected 302, got {r.status_code}: {r.text}"

    me = await c.get("/auth/me")
    body = me.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "new@x.c"
    assert body["user"]["roles"] == ["owner"]


@pytest.mark.anyio
async def test_callback_links_to_existing_local_user_by_verified_email(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider

    # Register first user via local auth (becomes owner)
    await _register(c, email="a@b.c", password="password123", display_name="A")
    await _logout(c)

    # Install google fake with same email
    await install_fake_provider(
        app,
        provider_id="p-google",
        provider_type="google",
        userinfo={"sub": "g-9", "email": "a@b.c", "email_verified": True, "name": "A"},
    )

    # OAuth callback should link to existing user
    r = await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code == 302, f"Expected 302, got {r.status_code}: {r.text}"

    me = await c.get("/auth/me")
    body = me.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "a@b.c"
    # Should be owner since we linked to the first-registered user
    assert body["user"]["roles"] == ["owner"]


@pytest.mark.anyio
async def test_callback_refuses_unverified_email(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider

    await install_fake_provider(
        app,
        provider_id="p-google",
        provider_type="google",
        userinfo={"sub": "g-2", "email": "unverified@x.c", "email_verified": False, "name": "Bad"},
    )

    r = await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"

    me = await c.get("/auth/me")
    assert me.json()["authenticated"] is False


@pytest.mark.anyio
async def test_returning_subject_logs_in_idempotently(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider

    await install_fake_provider(
        app,
        provider_id="p-google",
        provider_type="google",
        userinfo={"sub": "g-1", "email": "new@x.c", "email_verified": True, "name": "New"},
    )

    # First callback creates user
    r = await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code == 302

    # Logout
    await _logout(c)

    # Second callback should log in the same user via federated identity lookup
    r2 = await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    assert r2.status_code == 302

    me = await c.get("/auth/me")
    body = me.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "new@x.c"


@pytest.mark.anyio
async def test_github_uses_verified_primary_email(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider

    await install_fake_provider(
        app,
        provider_id="p-github",
        provider_type="github",
        github_user={"id": 555, "login": "gh", "name": "GH"},
        github_emails=[
            {"email": "sec@x.c", "verified": False, "primary": False},
            {"email": "main@x.c", "verified": True, "primary": True},
        ],
    )

    r = await c.get("/auth/oauth/p-github/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code == 302, f"Expected 302, got {r.status_code}: {r.text}"

    me = await c.get("/auth/me")
    body = me.json()
    assert body["authenticated"] is True
    assert body["user"]["email"] == "main@x.c"
