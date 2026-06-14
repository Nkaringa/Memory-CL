"""Integration tests for org invitations + per-repo grants (Task 8).

TDD: write failing tests first, then implement.
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
async def test_invite_then_new_user_accepts_with_credentials(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    inv = await client.post("/orgs/invitations", json={"email": "m@x.c", "role": "admin"})
    assert inv.status_code == 200
    token = inv.json()["invite_token"]
    await client.post("/auth/logout")
    # brand-new user accepts the invite by supplying credentials → created at invited role + logged in
    acc = await client.post("/auth/accept-invite", json={"token": token, "email": "m@x.c", "password": "password123", "display_name": "M"})
    assert acc.status_code == 200
    me = (await client.get("/auth/me")).json()
    assert me["authenticated"] is True and "admin" in me["user"]["roles"]


@pytest.mark.anyio
async def test_invite_invalid_token_400(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    await client.post("/auth/logout")
    r = await client.post("/auth/accept-invite", json={"token": "garbage", "email": "x@x.c", "password": "password123", "display_name": "X"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_logged_in_user_accepts_changes_role(client):
    # owner invites; a second user is created via accept (member by default would need creds) — here test the authenticated path:
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    inv = await client.post("/orgs/invitations", json={"email": "o@x.c", "role": "viewer"})
    token = inv.json()["invite_token"]
    # owner (still logged in) accepts an invite addressed to themselves at viewer → membership role becomes viewer
    acc = await client.post("/auth/accept-invite", json={"token": token})
    assert acc.status_code == 200


@pytest.mark.anyio
async def test_grant_crud(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    g = await client.post("/orgs/repos/repoA/grants", json={"subject_type": "team", "subject_id": "t1", "access": "write"})
    assert g.status_code == 200, g.text
    gid = g.json()["id"]
    lst = await client.get("/orgs/repos/repoA/grants")
    assert lst.json()["grants"][0]["access"] == "write"
    assert (await client.delete(f"/orgs/grants/{gid}")).status_code == 200
    assert (await client.get("/orgs/repos/repoA/grants")).json()["grants"] == []


@pytest.mark.anyio
async def test_invitations_list_and_revoke(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    inv = await client.post("/orgs/invitations", json={"email": "m@x.c", "role": "member"})
    iid = inv.json()["id"]
    assert len((await client.get("/orgs/invitations")).json()["invitations"]) == 1
    assert (await client.delete(f"/orgs/invitations/{iid}")).status_code == 200
