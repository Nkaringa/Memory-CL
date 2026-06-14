"""Admin CRUD tests for /config/auth/providers (Task 6, Phase-2 federation).

Tests:
- Full CRUD lifecycle: create, list (masked), enable, delete
- 422 validation: OIDC requires discovery_url
- Auth gate: create requires auth when configured
- PATCH updates fields
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
async def test_provider_crud_and_masking(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    create = await client.post("/config/auth/providers", json={
        "provider_type": "google", "display_name": "Google", "client_id": "cid", "client_secret": "shhh", "scopes": "openid email"})
    assert create.status_code == 200, create.text
    pid = create.json()["id"]
    lst = await client.get("/config/auth/providers")
    body = lst.json()["providers"][0]
    assert body["client_id"] == "cid"
    assert "client_secret" not in body
    assert body["has_secret"] is True
    assert body["enabled"] is False
    en = await client.post(f"/config/auth/providers/{pid}/enable", json={"enabled": True})
    assert en.status_code == 200
    assert (await client.get("/auth/providers")).json()["providers"][0]["id"] == pid
    assert (await client.delete(f"/config/auth/providers/{pid}")).status_code == 200
    assert (await client.get("/auth/providers")).json() == {"providers": []}


@pytest.mark.anyio
async def test_oidc_requires_discovery_url(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    r = await client.post("/config/auth/providers", json={"provider_type": "oidc", "display_name": "X", "client_id": "c", "client_secret": "s"})
    assert r.status_code == 422  # validation: oidc needs discovery_url


@pytest.mark.anyio
async def test_create_requires_auth_when_configured(client):
    await client.post("/config/mcp-key/generate")
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    await client.post("/auth/logout")
    r = await client.post("/config/auth/providers", json={"provider_type": "google", "display_name": "G", "client_id": "c", "client_secret": "s"})
    assert r.status_code in (401, 403)


@pytest.mark.anyio
async def test_update_changes_fields(client):
    await client.post("/auth/register", json={"email": "o@x.c", "password": "password123", "display_name": "O"})
    pid = (await client.post("/config/auth/providers", json={"provider_type": "google", "display_name": "G", "client_id": "c", "client_secret": "s"})).json()["id"]
    up = await client.patch(f"/config/auth/providers/{pid}", json={"display_name": "G2", "client_id": "c2", "client_secret": "s2", "scopes": "openid"})
    assert up.status_code == 200
    body = (await client.get("/config/auth/providers")).json()["providers"][0]
    assert body["display_name"] == "G2" and body["client_id"] == "c2"
