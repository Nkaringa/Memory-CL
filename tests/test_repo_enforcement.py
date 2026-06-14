"""TDD tests for per-repo access enforcement (Task 9, Phase-3 RBAC).

Non-breaking contract:
- auth NOT configured → open (all repo endpoints pass through)
- auth IS configured but principal is NOT authenticated → open
- auth IS configured AND principal IS authenticated → enforce RBAC

Uses the lite async client pattern from test_auth_enforcement.py so the
real SessionCache, user/session/grant repos, and runtime config are all
exercised end-to-end.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

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


def _make_repo(tmp_path: Path, name: str = "repo") -> Path:
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(textwrap.dedent("""
        def hello(): return 1
    """).lstrip())
    return d


# ---------------------------------------------------------------------------
# Test 1: open when unconfigured (no MCP key set)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_open_when_unconfigured(client: AsyncClient) -> None:
    """With no auth configured, repo endpoints are fully open."""
    # GET /repos should return 200 even with no auth
    r = await client.get("/repos")
    assert r.status_code == 200

    # POST /retrieve should NOT be 403 (it may 200 or fail for other reasons
    # like missing data, but must not enforce access control)
    r = await client.post("/retrieve", json={"text": "query", "repo_id": "x", "top_k": 5})
    assert r.status_code != 403


# ---------------------------------------------------------------------------
# Test 2: owner has full access (can retrieve their own repos)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_owner_has_full_access(client: AsyncClient, tmp_path: Path) -> None:
    """An org owner can access all repos in their org."""
    # Configure auth so enforcement kicks in
    gen = await client.post("/config/mcp-key/generate")
    assert gen.status_code == 200
    mcp_key = gen.json()["api_key"]

    # Register owner — auto-logs-in, gets session cookie
    reg = await client.post(
        "/auth/register",
        json={"email": "owner@x.c", "password": "password123", "display_name": "Owner"},
    )
    assert reg.status_code == 200

    # Ingest a repo as the owner (uses API key for ingest auth)
    repo_path = _make_repo(tmp_path, "owner-repo")
    ingest_r = await client.post(
        "/ingest",
        json={"repo_id": "owner-repo", "repo_path": str(repo_path), "commit_sha": "abc"},
        headers={"X-API-Key": mcp_key},
    )
    assert ingest_r.status_code == 200

    # Owner (session-authenticated) can list repos and see owner-repo
    list_r = await client.get("/repos")
    assert list_r.status_code == 200
    repo_ids = [r["repo_id"] for r in list_r.json()["repos"]]
    assert "owner-repo" in repo_ids

    # Owner can retrieve from owner-repo (no 403)
    retr_r = await client.post(
        "/retrieve",
        json={"text": "hello", "repo_id": "owner-repo", "top_k": 5},
    )
    assert retr_r.status_code != 403


# ---------------------------------------------------------------------------
# Test 3: member without grant is blocked from a specific repo
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_member_without_grant_blocked(client: AsyncClient, tmp_path: Path) -> None:
    """A member with NO grant on repoB is blocked (403) and repoB absent from listing."""
    # Configure auth
    gen = await client.post("/config/mcp-key/generate")
    mcp_key = gen.json()["api_key"]

    # Register owner
    await client.post(
        "/auth/register",
        json={"email": "owner@x.c", "password": "password123", "display_name": "Owner"},
    )

    # Ingest repoB under owner's session using API key
    repo_path = _make_repo(tmp_path, "repoB")
    ingest_r = await client.post(
        "/ingest",
        json={"repo_id": "repoB", "repo_path": str(repo_path), "commit_sha": "def"},
        headers={"X-API-Key": mcp_key},
    )
    assert ingest_r.status_code == 200

    # Invite a member
    inv = await client.post(
        "/orgs/invitations",
        json={"email": "member@x.c", "role": "member"},
    )
    assert inv.status_code == 200
    invite_token = inv.json()["invite_token"]

    # Log out owner
    await client.post("/auth/logout")

    # Member accepts invite (creates account + logs in)
    acc = await client.post(
        "/auth/accept-invite",
        json={
            "token": invite_token,
            "email": "member@x.c",
            "password": "password123",
            "display_name": "Member",
        },
    )
    assert acc.status_code == 200

    # Member should NOT see repoB in listing (no grant)
    list_r = await client.get("/repos")
    assert list_r.status_code == 200
    repo_ids = [r["repo_id"] for r in list_r.json()["repos"]]
    assert "repoB" not in repo_ids

    # Member should get 403 when trying to retrieve from repoB
    retr_r = await client.post(
        "/retrieve",
        json={"text": "hello", "repo_id": "repoB", "top_k": 5},
    )
    assert retr_r.status_code == 403


# ---------------------------------------------------------------------------
# Test 4: member with read grant can retrieve but not ingest
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_member_with_read_grant_can_retrieve_not_ingest(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Member with a read grant on repoC can /retrieve but not /ingest."""
    # Configure auth
    gen = await client.post("/config/mcp-key/generate")
    mcp_key = gen.json()["api_key"]

    # Register owner
    owner_reg = await client.post(
        "/auth/register",
        json={"email": "owner@x.c", "password": "password123", "display_name": "Owner"},
    )
    assert owner_reg.status_code == 200
    owner_data = (await client.get("/auth/me")).json()
    owner_uid = owner_data["user"]["user_id"]

    # Ingest repoC
    repo_path = _make_repo(tmp_path, "repoC")
    ingest_r = await client.post(
        "/ingest",
        json={"repo_id": "repoC", "repo_path": str(repo_path), "commit_sha": "ghi"},
        headers={"X-API-Key": mcp_key},
    )
    assert ingest_r.status_code == 200

    # Invite a member
    inv = await client.post(
        "/orgs/invitations",
        json={"email": "member@x.c", "role": "member"},
    )
    invite_token = inv.json()["invite_token"]

    # Log out owner
    await client.post("/auth/logout")

    # Member accepts invite
    acc = await client.post(
        "/auth/accept-invite",
        json={
            "token": invite_token,
            "email": "member@x.c",
            "password": "password123",
            "display_name": "Member",
        },
    )
    assert acc.status_code == 200
    member_uid = (await client.get("/auth/me")).json()["user"]["user_id"]

    # Log back in as owner to grant read access
    await client.post("/auth/logout")
    login_r = await client.post(
        "/auth/login",
        json={"email": "owner@x.c", "password": "password123"},
    )
    assert login_r.status_code == 200

    grant_r = await client.post(
        "/orgs/repos/repoC/grants",
        json={"subject_type": "user", "subject_id": member_uid, "access": "read"},
    )
    assert grant_r.status_code == 200

    # Log back in as member
    await client.post("/auth/logout")
    await client.post(
        "/auth/login",
        json={"email": "member@x.c", "password": "password123"},
    )

    # Member CAN retrieve (read grant)
    retr_r = await client.post(
        "/retrieve",
        json={"text": "hello", "repo_id": "repoC", "top_k": 5},
    )
    assert retr_r.status_code != 403

    # Member CANNOT ingest (needs write, only has read)
    ingest2_r = await client.post(
        "/ingest",
        json={"repo_id": "repoC", "repo_path": str(repo_path), "commit_sha": "jkl"},
        headers={"X-API-Key": mcp_key},
    )
    # ApiKeyDep would reject this first (401), OR our RBAC check gives 403.
    # Either way the member cannot ingest. The key belongs to the MCP layer,
    # not the human session. When a user is session-authenticated and presents
    # a valid MCP key, the key passes ApiKeyDep but RBAC still blocks with 403.
    # Without a valid key, ApiKeyDep blocks with 401. Both outcomes are correct.
    assert ingest2_r.status_code in (401, 403)
