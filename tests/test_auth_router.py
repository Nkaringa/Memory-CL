"""Integration tests for auth endpoints (register / login / logout / me)
and the get_principal dependency.

Uses the ASGI lifespan approach so the full FastAPI app (lite mode) starts
with real SQLite-backed repos and a real SessionCache, exercising the whole
auth stack end-to-end without mocks.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIRST = {"email": "a@b.c", "password": "password123", "display_name": "A"}
_SECOND = {"email": "b@b.c", "password": "password456", "display_name": "B"}


async def _register(client: AsyncClient, payload: dict | None = None) -> None:
    r = await client.post("/auth/register", json=payload or _FIRST)
    assert r.status_code == 200, r.text


async def _logout(client: AsyncClient) -> None:
    r = await client.post("/auth/logout")
    assert r.status_code == 200, r.text


async def _login(client: AsyncClient, payload: dict | None = None) -> None:
    r = await client.post("/auth/login", json=payload or {"email": _FIRST["email"], "password": _FIRST["password"]})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_me_anonymous(client: AsyncClient) -> None:
    r = await client.get("/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is False
    assert body["user"] is None


@pytest.mark.anyio
async def test_register_first_user_becomes_owner_and_logged_in(client: AsyncClient) -> None:
    r = await client.post("/auth/register", json=_FIRST)
    assert r.status_code == 200, r.text
    body = r.json()
    # First-user registration auto-logs the user in
    assert body["authenticated"] is True
    assert body["user"]["email"] == "a@b.c"
    assert body["user"]["roles"] == ["owner"]

    # Follow-up /me also shows the user still logged in (cookie was set)
    me = await client.get("/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["authenticated"] is True
    assert me_body["user"]["email"] == "a@b.c"
    assert me_body["user"]["roles"] == ["owner"]


@pytest.mark.anyio
async def test_login_logout_cycle(client: AsyncClient) -> None:
    await _register(client)

    # Logout
    r = await client.post("/auth/logout")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Now anonymous
    me = await client.get("/auth/me")
    assert me.json()["authenticated"] is False

    # Login again
    r = await client.post("/auth/login", json={"email": _FIRST["email"], "password": _FIRST["password"]})
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True

    # Back to authenticated
    me = await client.get("/auth/me")
    assert me.json()["authenticated"] is True


@pytest.mark.anyio
async def test_bad_password_rejected(client: AsyncClient) -> None:
    await _register(client)
    await _logout(client)

    r = await client.post("/auth/login", json={"email": _FIRST["email"], "password": "wrongpassword"})
    assert r.status_code == 401


@pytest.mark.anyio
async def test_second_anonymous_register_refused(client: AsyncClient) -> None:
    # Register + logout the first user
    await _register(client)
    await _logout(client)

    # While anonymous, try to register a second user — must be rejected
    r = await client.post("/auth/register", json=_SECOND)
    assert r.status_code in (401, 403)


@pytest.mark.anyio
async def test_owner_creates_second_user_keeps_own_session(client: AsyncClient) -> None:
    # bootstrap: first user becomes owner, auto-logged-in
    r = await client.post("/auth/register", json={"email": "owner@x.c", "password": "password123", "display_name": "Owner"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["authenticated"] is True
    assert body["user"]["roles"] == ["owner"]

    # owner (still logged in via cookie) creates a second user
    r2 = await client.post("/auth/register", json={"email": "second@x.c", "password": "password123", "display_name": "Second"})
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    # The new user is NOT auto-logged-in (not bootstrap); authenticated is False
    assert body2["authenticated"] is False
    assert body2["user"]["email"] == "second@x.c"
    assert body2["user"]["roles"] == ["member"]

    # the owner's OWN session is intact: /auth/me still shows the owner
    me = await client.get("/auth/me")
    me_body = me.json()
    assert me_body["authenticated"] is True
    assert me_body["user"]["email"] == "owner@x.c"
    assert me_body["user"]["roles"] == ["owner"]
