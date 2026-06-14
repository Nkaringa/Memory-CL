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


@pytest.fixture
async def client_and_app(tmp_path, monkeypatch):
    """Like `client`, but yields (AsyncClient, app) so tests can reach app.state."""
    monkeypatch.setenv("MODE", "lite")
    monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path))
    from core.config import get_settings
    get_settings.cache_clear()
    from apps.api.main import app
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c, app
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


# ---------------------------------------------------------------------------
# Test 5: cross-org isolation — org-A owner must NOT see org-B's repos
#
# This test EXPOSES THE BUG: when repo_registry is read from app.state
# (correct) vs app_state dataclass (wrong — always None → fallback lists
# every repo across all orgs).
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_cross_org_repo_isolation(client_and_app, tmp_path: Path) -> None:
    """An owner in org 'default' must not see or access repos registered to 'team-b'.

    Setup:
      1. Boot lite app. Register user → owner of org 'default'.
      2. Ingest repoA under the owner (lands in org 'default' registry).
      3. Directly insert repoB into repo_registry with org_id='team-b' (simulates
         a second org's repo without needing a full second-org user flow).
      4. Also insert a minimal ingestion unit for repoB so units_repo.list_repos()
         returns it — this is the condition that made the old fallback leak it.
      5. Assert:
         - GET /repos lists repoA but NOT repoB.
         - POST /retrieve {repo_id:'repoB'} → 403 (not leaked to default-org owner).
         - POST /retrieve {repo_id:'repoA'} → not 403 (own repo still accessible).
    """
    client, app = client_and_app

    # --- Step 1: configure auth + register owner --------------------------
    gen = await client.post("/config/mcp-key/generate")
    assert gen.status_code == 200
    mcp_key = gen.json()["api_key"]

    reg = await client.post(
        "/auth/register",
        json={"email": "owner@a.com", "password": "password123", "display_name": "Owner"},
    )
    assert reg.status_code == 200

    # --- Step 2: ingest repoA (registers it under org 'default') ----------
    repo_a_path = _make_repo(tmp_path, "repoA")
    ingest_r = await client.post(
        "/ingest",
        json={"repo_id": "repoA", "repo_path": str(repo_a_path), "commit_sha": "aaa"},
        headers={"X-API-Key": mcp_key},
    )
    assert ingest_r.status_code == 200

    # --- Step 3: inject repoB directly into repo_registry with org_id='team-b' ---
    # This simulates a second organisation's repo existing in the same database.
    repo_registry = app.state.repo_registry
    await repo_registry.upsert_local(
        repo_id="repoB", repo_path="/fake/repoB", commit_sha=None, org_id="team-b"
    )

    # --- Step 4: insert a minimal unit for repoB into units_repo so that
    #     units_repo.list_repos() returns BOTH repos. This is the critical
    #     condition: without org filtering, the fallback returns {repoA, repoB}
    #     and the owner sees both. With correct filtering (from registry),
    #     only repoA (org 'default') should be visible. ----------------------
    #
    # We write directly to the SQLite engine to avoid going through the full
    # ingest pipeline (which would need a real checkout + embedder).
    from sqlalchemy import text as sa_text
    app_state = app.state.app_state
    engine = app_state.units_repo._engine
    async with engine.begin() as conn:
        await conn.execute(
            sa_text(
                "INSERT OR IGNORE INTO ingestion_units "
                "(unit_id, repo_id, commit_sha, kind, name, qualified_name, "
                " file_path, language, line_start, line_end, content, source_sha, "
                " imports, calls, \"references\", bases, token_count, "
                " schema_version, created_at, updated_at, source) "
                "VALUES ('bbbb-0000-0000-0000-000000000000', 'repoB', 'bbb', "
                " 'function', 'fn', 'fake.fn', 'fake.py', 'python', 1, 1, "
                " 'def fn(): pass', 'sha_fake', '[]', '[]', '[]', '[]', 4, "
                " '1.0', datetime('now'), datetime('now'), 'local')"
            )
        )

    # Verify the unit was inserted so list_repos() sees repoB
    repos_in_units = await app_state.units_repo.list_repos()
    repo_ids_in_units = {r.repo_id for r in repos_in_units}
    assert "repoB" in repo_ids_in_units, "Setup: repoB must be in units_repo for this test to be meaningful"
    assert "repoA" in repo_ids_in_units

    # --- Step 5: assert cross-org isolation holds ------------------------
    # 5a. GET /repos should list repoA but NOT repoB
    list_r = await client.get("/repos")
    assert list_r.status_code == 200
    listed_ids = [r["repo_id"] for r in list_r.json()["repos"]]
    assert "repoA" in listed_ids, f"repoA missing from listing: {listed_ids}"
    assert "repoB" not in listed_ids, (
        f"CROSS-ORG LEAK: repoB (org=team-b) visible to default-org owner. "
        f"Listed repos: {listed_ids}"
    )

    # 5b. POST /retrieve on repoB → 403 (blocked — not in owner's org)
    retr_b = await client.post(
        "/retrieve",
        json={"text": "query", "repo_id": "repoB", "top_k": 5},
    )
    assert retr_b.status_code == 403, (
        f"CROSS-ORG LEAK: expected 403 for repoB, got {retr_b.status_code}. "
        "The registry must be read from request.app.state, not AppState dataclass."
    )

    # 5c. POST /retrieve on repoA → not 403 (own repo remains accessible)
    retr_a = await client.post(
        "/retrieve",
        json={"text": "query", "repo_id": "repoA", "top_k": 5},
    )
    assert retr_a.status_code != 403, (
        f"repoA (own repo) must not be blocked. Got {retr_a.status_code}"
    )
