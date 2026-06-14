"""Integration tests for /orgs members + teams management endpoints (Task 7).

Uses the lite async client pattern from tests/test_auth_router.py.
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
async def test_owner_lists_self_as_member(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    r = await client.get("/orgs/members")
    assert r.status_code == 200
    members = r.json()["members"]
    assert members[0]["email"] == "o@x.c" and members[0]["role"] == "owner"


@pytest.mark.anyio
async def test_owner_creates_team_and_adds_self(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    uid = (await client.get("/auth/me")).json()["user"]["user_id"]
    t = await client.post("/orgs/teams", json={"name": "Core", "slug": "core"})
    assert t.status_code == 200
    tid = t.json()["team_id"]
    add = await client.post(f"/orgs/teams/{tid}/members", json={"user_id": uid})
    assert add.status_code == 200
    mem = await client.get(f"/orgs/teams/{tid}/members")
    assert uid in [m["user_id"] for m in mem.json()["members"]]


@pytest.mark.anyio
async def test_duplicate_team_slug_409(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    await client.post("/orgs/teams", json={"name": "Core", "slug": "core"})
    dup = await client.post("/orgs/teams", json={"name": "Core2", "slug": "core"})
    assert dup.status_code == 409


@pytest.mark.anyio
async def test_members_requires_admin_when_configured(client):
    await client.post("/config/mcp-key/generate")
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    await client.post("/auth/logout")
    assert (await client.get("/orgs/members")).status_code in (401, 403)


@pytest.mark.anyio
async def test_cannot_demote_last_owner(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    uid = (await client.get("/auth/me")).json()["user"]["user_id"]
    r = await client.post(f"/orgs/members/{uid}/role", json={"role": "member"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_list_teams_empty(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    r = await client.get("/orgs/teams")
    assert r.status_code == 200
    assert r.json()["teams"] == []


@pytest.mark.anyio
async def test_delete_team(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    t = await client.post("/orgs/teams", json={"name": "Alpha", "slug": "alpha"})
    tid = t.json()["team_id"]
    d = await client.delete(f"/orgs/teams/{tid}")
    assert d.status_code == 200
    assert d.json()["ok"] is True
    r = await client.get("/orgs/teams")
    assert r.json()["teams"] == []


@pytest.mark.anyio
async def test_delete_team_not_in_org_404(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    d = await client.delete("/orgs/teams/nonexistent-team-id")
    assert d.status_code == 404


@pytest.mark.anyio
async def test_add_team_member_non_org_member_400(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    t = await client.post("/orgs/teams", json={"name": "Beta", "slug": "beta"})
    tid = t.json()["team_id"]
    add = await client.post(f"/orgs/teams/{tid}/members", json={"user_id": "not-a-member-id"})
    assert add.status_code == 400


@pytest.mark.anyio
async def test_remove_team_member(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    uid = (await client.get("/auth/me")).json()["user"]["user_id"]
    t = await client.post("/orgs/teams", json={"name": "Gamma", "slug": "gamma"})
    tid = t.json()["team_id"]
    await client.post(f"/orgs/teams/{tid}/members", json={"user_id": uid})
    rm = await client.delete(f"/orgs/teams/{tid}/members/{uid}")
    assert rm.status_code == 200
    assert rm.json()["ok"] is True
    mem = await client.get(f"/orgs/teams/{tid}/members")
    assert uid not in [m["user_id"] for m in mem.json()["members"]]


@pytest.mark.anyio
async def test_set_role_unknown_role_422(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    uid = (await client.get("/auth/me")).json()["user"]["user_id"]
    r = await client.post(f"/orgs/members/{uid}/role", json={"role": "superadmin"})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_set_role_no_membership_404(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    r = await client.post("/orgs/members/ghost-user-id/role", json={"role": "member"})
    assert r.status_code == 404


@pytest.mark.anyio
async def test_delete_member(client):
    """Register two users; owner removes the second member."""
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    # Owner is logged in — register second user while still authed as owner
    reg = await client.post("/auth/register", json={"email": "m@x.c", "password": "password456", "display_name": "M"})
    # reg returns the MeResponse for the second registered user (no auto-login for non-bootstrap)
    # so we get the ID from list_members instead
    members_r = await client.get("/orgs/members")
    second = next(m for m in members_r.json()["members"] if m["email"] == "m@x.c")
    second_uid = second["user_id"]
    r = await client.delete(f"/orgs/members/{second_uid}")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.anyio
async def test_cannot_remove_last_owner(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    uid = (await client.get("/auth/me")).json()["user"]["user_id"]
    r = await client.delete(f"/orgs/members/{uid}")
    assert r.status_code == 400
