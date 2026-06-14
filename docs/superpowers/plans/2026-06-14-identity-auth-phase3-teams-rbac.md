# Identity & Auth — Phase 3: Teams + per-repo RBAC — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organizations contain **teams**; users are invited into orgs; **per-repo grants** give teams/users access at read/write/admin; and repo operations on the **human path** are enforced against that access. **Agents (API-token/MCP) stay org-scoped with full access** to their org's repos (service-token model). Single-org deployments (today) are a no-op — current behavior preserved.

**Architecture:** Repos gain an `org_id` (default `default`). New repos: `teams`, `team_memberships`, `repo_grants`, `invitations` (pg + lite mirrors). A pure `RepoAccessResolver` computes a principal's accessible repos + level from (role, memberships, team grants, direct grants, repo→org map). Management endpoints (`/orgs/*`) are gated to org **owner/admin**. Enforcement wraps the human repo endpoints (`/repos`, `/retrieve`, `/repos/{id}/*`, `/ingest`); agents and owners/admins get full org access, members/viewers get only granted repos.

**Tech Stack:** FastAPI, SQLAlchemy async (pg + lite SQLite), Next.js. Spec: `docs/superpowers/specs/2026-06-13-identity-auth-design.md`. Builds on Phase 1 (PR #37) + Phase 2 (PR #38).

**Scope boundary:** Phase 3 = teams + invitations + grants + human-path enforcement. NOT in scope: per-repo restriction of agent tokens (agents are org-scoped-full by design decision), SCIM, multi-org-per-token. The `org switcher` is minimal (a user's active org is their session's `active_org_id`; switching = re-login or a `/auth/switch-org` endpoint — included as a small endpoint, no elaborate UI).

---

## Conventions (read once — same as Phases 1 & 2)

- **Lite parity mandatory.** Every `storage/<x>_repo.py` gets a `storage/lite/<x>_repo.py` mirror. pg `TIMESTAMPTZ`/`BOOLEAN`; lite `TEXT` ISO / `INTEGER 0|1`. Templates: `storage/membership_repo.py` (+lite), `storage/auth_provider_repo.py` (+lite, multi-row), `storage/federated_identity_repo.py` (+lite, UNIQUE-constraint).
- **No bare `assert`** → `if x is None: raise RuntimeError(...)`. `from __future__ import annotations`; `@dataclass(frozen=True, slots=True)`; `created_at: datetime | None = None`; lite reuses the row dataclass via import; `_parse_dt`/`bool(int)` at the boundary.
- **Tests offline & deterministic.** Repo tests use the lite engine fixture (`tests/test_org_repo.py`). Endpoint tests use the lite async client (`tests/test_auth_router.py`) with `@pytest.mark.anyio`. Wiring tests use the **sync** `TestClient` (`tests/test_auth_lifespan_wiring.py`) to avoid the async-teardown cancel-scope error.
- **Enforcement is non-breaking:** when no auth is configured (bootstrap/dev) the repo endpoints stay open; when a principal is present, owners/admins + agents get full org access, members/viewers get granted-only. A repo with no `repo_registry` row resolves to `org_id="default"`.
- Full suite: `python -m pytest -q`. UI gate: `cd ui && npm run build`. Commits: no AI attribution; single-line shell.

---

## Locked decisions

1. **Agents = org-scoped full access.** A `Principal` with `kind=="agent"` (or role `agent`) gets every repo in its org at `admin` level. No per-repo agent restriction. (Agents are `org_id="default"` today; multi-org agent tokens are a later concern.)
2. **Repo→org source of truth = `repo_registry.org_id`** (new column, default `"default"`). A repo with no registry row resolves to `"default"`. Ingest stamps the ingesting principal's `org_id`.
3. **Access levels** ordered `read < write < admin`. owner/admin (org role) ⇒ admin on all org repos. member/viewer ⇒ the max level granted via any of: a direct user grant, or a grant to a team they belong to. **viewer is capped at `read`** regardless of grant. No grant ⇒ no access.
4. **Management endpoints require org owner/admin** (`principal.has_role("owner") or principal.has_role("admin")`), via a new `require_org_admin` dependency.

---

## File Structure

**Create:**
- `core/auth/access.py` — `RepoAccessResolver` + `ACCESS_LEVELS`/`level_at_least()` + `accessible_repos()` pure logic
- `storage/team_repo.py` + `storage/lite/team_repo.py` — teams + team_memberships
- `storage/repo_grant_repo.py` + `storage/lite/repo_grant_repo.py`
- `storage/invitation_repo.py` + `storage/lite/invitation_repo.py`
- `apps/api/routers/orgs.py` — members/teams/grants/invitations admin + accept + switch-org
- `apps/api/repo_access.py` — `require_repo_access(level)` dependency factory + `accessible_repo_ids(principal, ...)` helper that loads from repos
- `schemas/orgs.py` — request/response models
- UI: `ui/components/settings/OrganizationPanel.tsx` (members + teams + invitations) + a repo-grants control on the repositories page
- Test files per task

**Modify:**
- `storage/repo_registry_repo.py` + `storage/lite/repo_registry_repo.py` — add `org_id`
- `storage/repositories.py` — Protocols for 3 new repos
- `apps/api/state.py`, `apps/api/lifespan.py`, `apps/api/dependencies.py` — wire 3 repos
- `apps/api/routers/ingest.py` — stamp org_id + require write access
- `apps/api/routers/retrieve.py`, `apps/api/routers/repos.py` — enforce access (filter/gate)
- `apps/api/routers/auth.py` — `provision_user` already exists; invitation-accept uses it/ membership add
- `apps/api/main.py` — include orgs router
- `ui/lib/orgs.ts`, `ui/lib/types.ts`, `ui/app/(main)/settings/page.tsx`, `ui/app/(auth)/login/page.tsx` (invite-accept entry), `ui/app/(main)/repositories/`

---

## Task 1: Repo → org ownership (`org_id` on repo_registry)

**Files:** Modify `storage/repo_registry_repo.py` + `storage/lite/repo_registry_repo.py`; Test `tests/test_repo_registry_org.py`

Add `org_id TEXT NOT NULL DEFAULT 'default'` to the `repo_registry` table + `RepoRegistryRow.org_id`. Idempotent migration for the already-deployed table: `ALTER TABLE repo_registry ADD COLUMN IF NOT EXISTS org_id TEXT NOT NULL DEFAULT 'default'` (Postgres) — follow how `app_config` added `webhook_secret` in Phase 3b (idempotent ADD COLUMN). For lite, `ensure_schema` recreates/migrates; add the column with default. The upsert methods (`upsert_local`, `upsert_managed`, or whatever exists — READ the file) gain an `org_id: str = "default"` parameter recorded on insert (keep existing call sites working with the default).

- [ ] **Step 1: Failing test** (lite fixture). Read `storage/lite/repo_registry_repo.py` first to learn the real method names (e.g. `upsert_local`, `get`, `list_all`). Test that a row can be created with an org_id and read back, and that the default is `"default"`:

```python
import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.repo_registry_repo import SqliteRepoRegistryRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "r.db"))
    r = SqliteRepoRegistryRepository(engine)
    await r.ensure_schema()
    return r

async def test_org_id_defaults_and_roundtrips(repo):
    # use the repo's real registration method (adapt to actual signature)
    await repo.upsert_local(repo_id="acme", org_id="team-1", repo_path="/x", commit_sha=None)
    row = await repo.get("acme")
    assert row is not None and row.org_id == "team-1"

async def test_default_org_when_unspecified(repo):
    await repo.upsert_local(repo_id="acme2", repo_path="/x", commit_sha=None)
    assert (await repo.get("acme2")).org_id == "default"
```

- [ ] **Step 2: Run → fail.** Step 3: add the column to both DDLs + `RepoRegistryRow.org_id` (place it right after `repo_id`; trailing-default the field so existing constructions don't break — actually org_id is required-with-default in DB; in the dataclass give it `org_id: str = "default"` so any in-code construction still works). Thread `org_id` through the upsert method(s) (default `"default"`). Update the SELECT column lists + row hydration in both pg and lite.

- [ ] **Step 4: Run → pass; FULL suite** (existing repo_registry tests + freshness tests must stay green — they call the upsert methods; the new param defaults so they keep working). **Step 5: Commit** `git add -A && git commit -m "feat(storage): repo_registry.org_id (repos belong to orgs; default 'default')"`

---

## Task 2: Teams + team memberships repository (pg + lite)

**Files:** Modify `storage/repositories.py`; Create `storage/team_repo.py`, `storage/lite/team_repo.py`; Test `tests/test_team_repo.py`

Two tables. `teams`: `team_id TEXT PK, org_id TEXT, name TEXT, slug TEXT, created_at`, `UNIQUE(org_id, slug)`. `team_memberships`: `team_id TEXT, user_id TEXT, created_at`, `PRIMARY KEY(team_id, user_id)`.

- [ ] **Step 1: Failing test** (lite fixture):

```python
import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.team_repo import SqliteTeamRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "t.db"))
    r = SqliteTeamRepository(engine)
    await r.ensure_schema()
    return r

async def test_create_team_and_list_for_org(repo):
    t = await repo.create_team(team_id="t1", org_id="acme", name="Core", slug="core")
    assert t.slug == "core"
    assert [x.team_id for x in await repo.list_teams(org_id="acme")] == ["t1"]

async def test_add_member_and_list(repo):
    await repo.create_team(team_id="t1", org_id="acme", name="Core", slug="core")
    await repo.add_team_member(team_id="t1", user_id="u1")
    await repo.add_team_member(team_id="t1", user_id="u2")
    assert {m for m in await repo.list_team_member_ids("t1")} == {"u1", "u2"}

async def test_team_ids_for_user(repo):
    await repo.create_team(team_id="t1", org_id="acme", name="C", slug="core")
    await repo.create_team(team_id="t2", org_id="acme", name="D", slug="data")
    await repo.add_team_member(team_id="t1", user_id="u1")
    await repo.add_team_member(team_id="t2", user_id="u1")
    assert set(await repo.team_ids_for_user(user_id="u1", org_id="acme")) == {"t1", "t2"}

async def test_remove_member_and_delete_team(repo):
    await repo.create_team(team_id="t1", org_id="acme", name="C", slug="core")
    await repo.add_team_member(team_id="t1", user_id="u1")
    await repo.remove_team_member(team_id="t1", user_id="u1")
    assert await repo.list_team_member_ids("t1") == []
    await repo.delete_team("t1")
    assert await repo.get_team("t1") is None
```

- [ ] **Step 2: Run → fail. Step 3: Protocol** `TeamRepository` (ensure_schema, create_team, get_team, list_teams(org_id), delete_team, add_team_member, remove_team_member, list_team_member_ids(team_id), team_ids_for_user(user_id, org_id)). `TeamRow(team_id, org_id, name, slug, created_at)`. **Step 4: Postgres impl** (`delete_team` cascades team_memberships — delete memberships then the team, or rely on FK; simplest: explicit delete of memberships in delete_team). **Step 5: Lite impl** (reuse TeamRow). **Step 6: export.** **Step 7: run pass + full suite. Step 8: Commit** `git add -A && git commit -m "feat(storage): teams + team_memberships repo (pg + lite)"`

---

## Task 3: Repo grants repository (pg + lite)

**Files:** Modify `storage/repositories.py`; Create `storage/repo_grant_repo.py`, `storage/lite/repo_grant_repo.py`; Test `tests/test_repo_grant_repo.py`

Table `repo_grants`: `id TEXT PK, org_id TEXT, repo_id TEXT, subject_type TEXT ('team'|'user'), subject_id TEXT, access TEXT ('read'|'write'|'admin'), created_at`, `UNIQUE(repo_id, subject_type, subject_id)` (one grant per subject per repo; re-grant updates the level).

- [ ] **Step 1: Failing test:**

```python
import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.repo_grant_repo import SqliteRepoGrantRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "g.db"))
    r = SqliteRepoGrantRepository(engine)
    await r.ensure_schema()
    return r

async def test_grant_and_list_for_repo(repo):
    g = await repo.grant(id="g1", org_id="acme", repo_id="r1", subject_type="team", subject_id="t1", access="write")
    assert g.access == "write"
    rows = await repo.list_for_repo(repo_id="r1")
    assert len(rows) == 1 and rows[0].subject_id == "t1"

async def test_regrant_updates_level(repo):
    await repo.grant(id="g1", org_id="acme", repo_id="r1", subject_type="team", subject_id="t1", access="read")
    await repo.grant(id="g2", org_id="acme", repo_id="r1", subject_type="team", subject_id="t1", access="admin")
    rows = await repo.list_for_repo(repo_id="r1")
    assert len(rows) == 1 and rows[0].access == "admin"   # UNIQUE upsert

async def test_list_for_subjects(repo):
    await repo.grant(id="g1", org_id="acme", repo_id="r1", subject_type="user", subject_id="u1", access="read")
    await repo.grant(id="g2", org_id="acme", repo_id="r2", subject_type="team", subject_id="t1", access="write")
    rows = await repo.list_for_subjects(org_id="acme", user_id="u1", team_ids=["t1"])
    assert {(r.repo_id, r.access) for r in rows} == {("r1", "read"), ("r2", "write")}

async def test_revoke(repo):
    await repo.grant(id="g1", org_id="acme", repo_id="r1", subject_type="user", subject_id="u1", access="read")
    await repo.revoke("g1")
    assert await repo.list_for_repo(repo_id="r1") == []
```

- [ ] **Step 2: Run → fail. Step 3: Protocol** `RepoGrantRepository`: ensure_schema, grant (UPSERT on UNIQUE(repo_id,subject_type,subject_id) → DO UPDATE SET access, returning the row), get(id), list_for_repo(repo_id), `list_for_subjects(*, org_id, user_id, team_ids: list[str])` (grants where (subject_type='user' AND subject_id=user_id) OR (subject_type='team' AND subject_id IN team_ids)), revoke(id), delete_for_repo(repo_id). `RepoGrantRow(id, org_id, repo_id, subject_type, subject_id, access, created_at)`. **Step 4/5: pg + lite.** For `list_for_subjects` in lite, build the IN clause safely (parametrized) — empty team_ids must still work (just the user grants). **Step 6: export. Step 7: pass + full suite. Step 8: Commit** `git add -A && git commit -m "feat(storage): repo_grants repo (pg + lite)"`

---

## Task 4: Invitations repository (pg + lite)

**Files:** Modify `storage/repositories.py`; Create `storage/invitation_repo.py`, `storage/lite/invitation_repo.py`; Test `tests/test_invitation_repo.py`

Table `invitations`: `id TEXT PK, org_id TEXT, email TEXT, role TEXT, token_hash TEXT UNIQUE, status TEXT ('pending'|'accepted'|'revoked'), invited_by TEXT, expires_at, created_at`. Store only the SHA-256 hash of the invite token (raw shown once in the link). Use epoch INTEGER for `expires_at` in lite (like sessions) for correct expiry compares.

- [ ] **Step 1: Failing test:**

```python
import pytest
from datetime import datetime, timezone, timedelta
from storage.lite.engine import make_sqlite_engine
from storage.lite.invitation_repo import SqliteInvitationRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "i.db"))
    r = SqliteInvitationRepository(engine)
    await r.ensure_schema()
    return r

def _exp(s): return datetime.now(timezone.utc) + timedelta(seconds=s)

async def test_create_and_get_pending_by_hash(repo):
    inv = await repo.create(id="i1", org_id="acme", email="a@b.c", role="member", token_hash="h1", invited_by="u0", expires_at=_exp(3600))
    assert inv.status == "pending"
    got = await repo.get_pending_by_hash("h1")
    assert got is not None and got.org_id == "acme" and got.role == "member"

async def test_expired_pending_not_returned(repo):
    await repo.create(id="i2", org_id="acme", email="a@b.c", role="member", token_hash="h2", invited_by="u0", expires_at=_exp(-1))
    assert await repo.get_pending_by_hash("h2") is None

async def test_mark_accepted_then_not_pending(repo):
    await repo.create(id="i3", org_id="acme", email="a@b.c", role="member", token_hash="h3", invited_by="u0", expires_at=_exp(3600))
    await repo.mark_accepted("i3")
    assert await repo.get_pending_by_hash("h3") is None

async def test_list_and_revoke(repo):
    await repo.create(id="i4", org_id="acme", email="x@y.z", role="admin", token_hash="h4", invited_by="u0", expires_at=_exp(3600))
    assert len(await repo.list_for_org("acme")) == 1
    await repo.revoke("i4")
    assert await repo.get_pending_by_hash("h4") is None
```

- [ ] **Step 2: Run → fail. Step 3: Protocol** `InvitationRepository`: ensure_schema, create, get_pending_by_hash (status='pending' AND not expired), list_for_org(org_id), mark_accepted(id), revoke(id). `InvitationRow(id, org_id, email, role, token_hash, status, invited_by, expires_at, created_at)`. **Step 4/5: pg + lite** (lite epoch INTEGER for expires_at; `get_pending_by_hash` filters status + `expires_at > :now` with a Python-computed now). **Step 6: export. Step 7: pass + full suite. Step 8: Commit** `git add -A && git commit -m "feat(storage): invitations repo (pg + lite)"`

---

## Task 5: RepoAccessResolver (pure logic — the heart of RBAC)

**Files:** Create `core/auth/access.py`; Test `tests/test_repo_access_resolver.py`

Pure, dependency-free resolution. No DB — takes already-loaded data. (The dependency in Task 9 loads the data and calls this.)

- [ ] **Step 1: Failing test:**

```python
from core.auth.access import (ACCESS_LEVELS, level_at_least, resolve_repo_access, accessible_repo_ids)

def test_level_ordering():
    assert ACCESS_LEVELS == ("read", "write", "admin")
    assert level_at_least("write", "read") and not level_at_least("read", "write")

def test_agent_gets_admin_on_all_org_repos():
    acc = resolve_repo_access(kind="agent", role="agent", org_repo_ids={"r1","r2"},
                              user_id="agent", team_ids=set(), grants=[])
    assert acc == {"r1": "admin", "r2": "admin"}

def test_owner_admin_get_admin_on_all():
    for role in ("owner", "admin"):
        acc = resolve_repo_access(kind="user", role=role, org_repo_ids={"r1","r2"},
                                  user_id="u1", team_ids=set(), grants=[])
        assert acc == {"r1": "admin", "r2": "admin"}

def test_member_gets_granted_only_max_level():
    grants = [{"repo_id":"r1","access":"read"}, {"repo_id":"r1","access":"write"}, {"repo_id":"r2","access":"read"}]
    acc = resolve_repo_access(kind="user", role="member", org_repo_ids={"r1","r2","r3"},
                              user_id="u1", team_ids={"t1"}, grants=grants)
    assert acc == {"r1": "write", "r2": "read"}   # r3 not granted → absent

def test_viewer_capped_at_read():
    grants = [{"repo_id":"r1","access":"admin"}]
    acc = resolve_repo_access(kind="user", role="viewer", org_repo_ids={"r1"},
                              user_id="u1", team_ids=set(), grants=grants)
    assert acc == {"r1": "read"}

def test_grants_outside_org_repos_ignored():
    grants = [{"repo_id":"rX","access":"admin"}]   # rX not in org_repo_ids
    acc = resolve_repo_access(kind="user", role="member", org_repo_ids={"r1"},
                              user_id="u1", team_ids=set(), grants=grants)
    assert acc == {}

def test_accessible_ids_filter_by_level():
    acc = {"r1":"read","r2":"write","r3":"admin"}
    assert accessible_repo_ids(acc, need="write") == {"r2","r3"}
```

- [ ] **Step 2: Run → fail. Step 3: Implement** `core/auth/access.py`:
  - `ACCESS_LEVELS = ("read","write","admin")`; `_RANK = {l:i for i,l in enumerate(ACCESS_LEVELS)}`.
  - `level_at_least(have, need) -> bool`: `_RANK[have] >= _RANK[need]`.
  - `max_level(a, b) -> str`: higher rank.
  - `resolve_repo_access(*, kind, role, org_repo_ids: set[str], user_id, team_ids: set[str], grants: list[dict]) -> dict[str,str]`:
    - if `kind == "agent"` or `role in ("owner","admin")`: return `{rid: "admin" for rid in org_repo_ids}`.
    - else: fold `grants` (each `{"repo_id","access"}` — the caller pre-filters grants to this user + their teams via `list_for_subjects`): for each grant whose `repo_id in org_repo_ids`, accumulate `max_level`. If `role == "viewer"`, cap every resulting level at `"read"`. Return the dict (repos with no grant are absent).
  - `accessible_repo_ids(access: dict[str,str], *, need: str = "read") -> set[str]`: `{rid for rid,lvl in access.items() if level_at_least(lvl, need)}`.
- [ ] **Step 4: Run → pass. Step 5: Commit** `git add -A && git commit -m "feat(auth): RepoAccessResolver (pure per-repo access logic)"`

---

## Task 6: Wire the 3 new repos into state/lifespan/deps

**Files:** Modify `apps/api/state.py`, `apps/api/lifespan.py`, `apps/api/dependencies.py`; Test `tests/test_rbac_wiring.py`

Mirror the Phase-1/2 wiring exactly. Add `team_repo`, `repo_grant_repo`, `invitation_repo` as `... | None = None` AppState fields, construct pg in `_build_state` + lite in `_build_lite_state`, `ensure_schema()` all three in the lifespan identity block, add `dependencies.py` getters + Dep aliases.

- [ ] **Step 1: Failing test** (sync TestClient lite, copy `tests/test_auth_lifespan_wiring.py`): assert `app.state.app_state.team_repo / repo_grant_repo / invitation_repo` are not None after boot. **Step 2: fail. Step 3-4: wire (server + lite). Step 5: deps. Step 6: run pass + FULL suite.** **Step 7: Commit** `git add -A && git commit -m "feat(api): wire team/grant/invitation repos into lifespan (pg + lite)"`

---

## Task 7: `require_org_admin` + members & teams management endpoints

**Files:** Create `apps/api/routers/orgs.py`, `schemas/orgs.py`; Modify `apps/api/main.py`; Test `tests/test_orgs_members_teams.py`

`router = APIRouter(prefix="/orgs", tags=["orgs"])`. A `require_org_admin` dependency: resolves the Principal (PrincipalDep), raises 403 unless `principal.is_authenticated and (principal.has_role("owner") or principal.has_role("admin"))`. (Agents: `kind=="agent"` → treat as admin of their org, so the org API is usable by an org service token too.)

Endpoints (all under the org from `principal.org_id`):
- Members: `GET /orgs/members` (list memberships in principal.org_id → join user email/display via user_repo), `POST /orgs/members/{user_id}/role {role}` (set_role; cannot demote the last owner — guard: if target is the only owner and new role != owner → 400), `DELETE /orgs/members/{user_id}` (remove membership; cannot remove the last owner).
- Teams: `GET /orgs/teams`, `POST /orgs/teams {name, slug}`, `DELETE /orgs/teams/{team_id}`, `POST /orgs/teams/{team_id}/members {user_id}` (must be an org member), `DELETE /orgs/teams/{team_id}/members/{user_id}`, `GET /orgs/teams/{team_id}/members`.

- [ ] **Step 1: Failing tests** (lite async client; register first user = owner → authed). Cover: list members shows the owner; create a 2nd user (via a 2nd register won't work — they'd be member; OR seed via invitation later — for THIS test, just test team CRUD + member role-set on the owner + the last-owner guard). Example:

```python
async def test_owner_can_create_team_and_add_self(client):
    await client.post("/auth/register", json={"email":"o@x.c","password":"password123","display_name":"O"})
    me = (await client.get("/auth/me")).json()["user"]
    t = await client.post("/orgs/teams", json={"name":"Core","slug":"core"})
    assert t.status_code == 200
    tid = t.json()["team_id"]
    add = await client.post(f"/orgs/teams/{tid}/members", json={"user_id": me["user_id"]})
    assert add.status_code == 200
    mem = await client.get(f"/orgs/teams/{tid}/members")
    assert me["user_id"] in [m["user_id"] for m in mem.json()["members"]]

async def test_members_endpoint_requires_admin(client):
    # configure a key, register+logout → anonymous can't list members
    await client.post("/config/mcp-key/generate")
    await client.post("/auth/register", json={"email":"o@x.c","password":"password123","display_name":"O"})
    await client.post("/auth/logout")
    assert (await client.get("/orgs/members")).status_code in (401, 403)

async def test_cannot_demote_last_owner(client):
    reg = await client.post("/auth/register", json={"email":"o@x.c","password":"password123","display_name":"O"})
    uid = (await client.get("/auth/me")).json()["user"]["user_id"]
    r = await client.post(f"/orgs/members/{uid}/role", json={"role":"member"})
    assert r.status_code == 400   # last owner can't be demoted
```

- [ ] **Step 2-4:** Implement schemas + endpoints + `require_org_admin`; include router in main.py. The last-owner guard: count owners in the org via `membership_repo.list_members(org_id)`; block if it would drop to zero. **Step 5: run pass + full suite.** **Step 6: Commit** `git add -A && git commit -m "feat(api): /orgs members + teams management (org-admin gated)"`

---

## Task 8: Invitations + per-repo grants endpoints

**Files:** Modify `apps/api/routers/orgs.py`, `schemas/orgs.py`, `apps/api/routers/auth.py` (accept), `apps/api/main.py`; Test `tests/test_orgs_invitations_grants.py`

- Invitations (org-admin gated): `POST /orgs/invitations {email, role}` → mint raw token (`secrets.token_urlsafe(24)`), store `hash_session_token(raw)` (reuse from auth_deps) as token_hash, `expires_at=now+7d`, return `{invite_token: raw, accept_path: "/auth/accept-invite?token="+raw}` ONCE. `GET /orgs/invitations` (list pending). `DELETE /orgs/invitations/{id}` (revoke).
- Accept (authenticated, NOT admin-gated): `POST /auth/accept-invite {token}` → `get_pending_by_hash(hash(token))`; 400 if none/expired; if the **current** principal is authenticated → add a membership for `principal.user_id` in the invite's org at the invite's role (if a membership already exists, update role or no-op); `mark_accepted`. Return the updated MeResponse-ish. (Edge: the email on the invite is informational; binding is to whoever is logged in and accepts — document this. A stricter email-match is a later hardening.)
- Grants (org-admin gated): `POST /orgs/repos/{repo_id}/grants {subject_type, subject_id, access}` (validate access ∈ read/write/admin, subject_type ∈ team/user; the repo must be in principal.org_id — check repo_registry.org_id, default "default"), `GET /orgs/repos/{repo_id}/grants`, `DELETE /orgs/grants/{grant_id}`.

- [ ] **Step 1: Failing tests** (lite async client). Cover: owner creates an invite (gets a token), a SECOND user registers (becomes member since owner exists) then accepts the invite to get the invited role; owner grants a team access to a repo and lists it. Example sketch:

```python
async def test_invite_create_and_accept_changes_role(client):
    await client.post("/auth/register", json={"email":"o@x.c","password":"password123","display_name":"O"})  # owner
    inv = await client.post("/orgs/invitations", json={"email":"m@x.c","role":"admin"})
    token = inv.json()["invite_token"]
    await client.post("/auth/logout")
    # second user registers (member), then accepts the admin invite
    await client.post("/auth/register", json={"email":"m@x.c","password":"password123","display_name":"M"})
    acc = await client.post("/auth/accept-invite", json={"token": token})
    assert acc.status_code == 200
    assert "admin" in (await client.get("/auth/me")).json()["user"]["roles"]

async def test_grant_crud(client):
    await client.post("/auth/register", json={"email":"o@x.c","password":"password123","display_name":"O"})
    g = await client.post("/orgs/repos/repoA/grants", json={"subject_type":"team","subject_id":"t1","access":"write"})
    assert g.status_code == 200
    lst = await client.get("/orgs/repos/repoA/grants")
    assert lst.json()["grants"][0]["access"] == "write"
```

- [ ] **Step 2-4:** Implement. (Note: registering a 2nd user requires being an admin or bootstrap per Phase-1 register rule — CHECK: Phase 1 made `register` require owner/admin once users exist. So "second user registers" in the test will FAIL unless logged in as admin. ADAPT: the invitation flow should allow a NEW user to self-register via the invite. Implement `POST /auth/accept-invite` to ALSO work for a brand-new user: accept body `{token, email?, password?, display_name?}` — if no current session and credentials provided + the invite is valid, `provision_user` (no owner bootstrap — force the invited role) then create a session then add membership. If a session exists, just add/Update membership. This makes invites the real "add a teammate" path. Update the test accordingly: accept-invite with credentials creates the user+session+membership at the invited role.) Reuse `provision_user` but pass an explicit role override — extend `provision_user` with an optional `role: str | None = None` (when set, skip the owner/bootstrap logic and use that role). Keep existing callers unchanged (role=None → current behavior).
- [ ] **Step 5: run pass + full suite (existing auth tests must stay green — the provision_user change is additive). Step 6: Commit** `git add -A && git commit -m "feat(api): org invitations (self-serve accept) + per-repo grants"`

---

## Task 9: Enforcement on the human repo path

**Files:** Create `apps/api/repo_access.py`; Modify `apps/api/routers/repos.py`, `apps/api/routers/retrieve.py`, `apps/api/routers/ingest.py`; Test `tests/test_repo_enforcement.py`

`apps/api/repo_access.py`:
- `async def load_access_for_principal(principal, *, app_state) -> dict[str,str]`: gather `org_repo_ids` = repo_ids whose `repo_registry.org_id == principal.org_id` UNION any repo with no registry row treated as `"default"` (so if principal.org_id=="default", include registry-less repos). Practically: `all_repo_ids = {s.repo_id for s in await units_repo.list_repos()}`; `reg = {row.repo_id: row.org_id for row in await repo_registry.list_all()}`; `org_repo_ids = {rid for rid in all_repo_ids if reg.get(rid, "default") == principal.org_id}`. Then `team_ids = set(await team_repo.team_ids_for_user(principal.user_id, principal.org_id))`; `grants = [{"repo_id":g.repo_id,"access":g.access} for g in await grant_repo.list_for_subjects(org_id=principal.org_id, user_id=principal.user_id, team_ids=list(team_ids))]`; return `resolve_repo_access(kind=principal.kind, role=(principal.roles[0] if principal.roles else ""), org_repo_ids=org_repo_ids, user_id=principal.user_id, team_ids=team_ids, grants=grants)`.
- `def require_repo_access(level: str)` → a FastAPI dependency factory returning a dependency that: resolves the principal (soft), and **if auth is not configured (bootstrap/dev) OR principal not authenticated-and-no-key** → allow (non-breaking; matches existing open behavior) ; else loads access and checks the path/has the repo at `level`, else 403. Because repo_id arrives differently per route (path vs body), make a helper `await assert_repo_access(principal, repo_id, level, app_state)` and call it inside each handler rather than a one-size dependency.

Apply:
- `GET /repos` → filter the returned list to `accessible_repo_ids(access, need="read")` when a principal is present & auth configured; otherwise return all (current behavior). Agents/owner/admin → all org repos.
- `GET /repos/{repo_id}/qnames` + `/graph`, `POST /retrieve` → `assert_repo_access(principal, repo_id, "read", ...)`.
- `POST /ingest` + `/ingest/reembed` → `assert_repo_access(principal, repo_id, "write", ...)` AND stamp `org_id=principal.org_id` on the registry upsert (new ingests join the caller's org; agents → default).

**Non-breaking contract (test it):** with NO auth configured, every repo endpoint behaves exactly as before (open). With a logged-in owner, full access. With a member who has only a read grant on r1, `/retrieve` on r1 works, `/retrieve` on r2 (ungranted) → 403, and `/ingest` on r1 → 403 (read < write).

- [ ] **Step 1: Failing tests** `tests/test_repo_enforcement.py` (lite async client). Build the scenario via the repos directly through app.state (extend the fixture to yield app) OR via the /orgs endpoints: owner registers; owner ingests repoA (becomes default org, owner has admin); create a member (via invite-accept) ; grant the member read on repoA via a team they're in; assert member can /retrieve repoA, cannot /ingest repoA, cannot /retrieve repoB. Also: `test_open_when_unconfigured` — fresh app, no register, `/retrieve` works (open).
- [ ] **Step 2-4:** Implement. Be careful: `/retrieve` and `/repos` are currently unauthenticated — inject `SoftPrincipalDep` (never raises) and the soft api-key; treat "auth configured" via the same helper config.py uses (`auth_is_configured`). Keep the open path identical to today when unconfigured.
- [ ] **Step 5: FULL suite green (existing retrieve/repos/ingest tests must pass — they run unconfigured, so the open path keeps them green). Step 6: Commit** `git add -A && git commit -m "feat(api): enforce per-repo access on human repo path (non-breaking; agents org-full)"`

---

## Task 10: UI — Organization panel (members, teams, invitations) + repo grants

**Files:** Create `ui/components/settings/OrganizationPanel.tsx`; Modify `ui/lib/orgs.ts` (new), `ui/lib/types.ts`, `ui/app/(main)/settings/page.tsx`, `ui/app/(main)/repositories/` (grants control), `ui/app/(auth)/login/page.tsx` or a small `/accept-invite` page. Build gate: `cd ui && npm run build`.

- [ ] **Step 1:** `ui/lib/orgs.ts`: typed helpers for `/orgs/members`, `/orgs/teams` (+members), `/orgs/invitations` (+accept), `/orgs/repos/{id}/grants`.
- [ ] **Step 2:** `OrganizationPanel` in Settings (emerald, mirror IdentityPanel): Members list (email, role dropdown owner/admin/member/viewer, remove), Teams (create, list, add/remove members), Invitations (create by email+role → show the accept link once + copy, list pending, revoke). Hide the whole panel if the current user isn't owner/admin (call /auth/me roles).
- [ ] **Step 3:** Accept-invite entry: a route/page (e.g. `/accept-invite?token=...`) that, if logged in, POSTs `/auth/accept-invite {token}`; if not, shows a register+accept form (email/password/display_name + token) → POST accept-invite with credentials. Redirect to `/` on success.
- [ ] **Step 4:** Repositories page: for owner/admin, a small "Manage access" control per repo → list/add/remove grants (team/user + level). Keep minimal.
- [ ] **Step 5:** `cd ui && npm run build` must pass. **Step 6: Commit** `git add -A && git commit -m "feat(ui): Organization settings (members, teams, invitations) + repo grants"`

---

## Task 11: Docs + final gates

**Files:** Modify `docs/22_SECURITY_AND_ACCESS_CONTROL.md`, `docs/07_API_REFERENCE.md`, `docs/26_GLOSSARY.md`, `docs/27_FEATURE_MATRIX.md`

- [ ] **Step 1:** `22_SECURITY`: add "Teams + per-repo RBAC (Phase 3)" — the access model (owner/admin ⇒ all org repos; member/viewer ⇒ granted via team/direct, viewer capped at read; agents ⇒ org-scoped full), repos belong to orgs, invitations (self-serve accept), the enforcement points, and the explicit note that **agents are org-scoped full-access by design** (not per-repo). Mark the identity milestone (Phases 1-3) complete.
- [ ] **Step 2:** `07_API_REFERENCE`: add the `/orgs/*` suite + `/auth/accept-invite`.
- [ ] **Step 3:** `26_GLOSSARY`: Team, Repo grant, Invitation, Access level (read/write/admin), RBAC.
- [ ] **Step 4:** `27_FEATURE_MATRIX`: flip team-tier auth / RBAC to functional.
- [ ] **Step 5: Gates.** `python -m pytest tests/ -q` (green). `cd ui && npm run build` (clean). Lite boot route check for `/orgs/*`.
- [ ] **Step 6: Commit** `git add -A && git commit -m "docs: document Phase-3 teams + per-repo RBAC (identity milestone complete)"`

---

## Done criteria (Phase 3)

- Repos belong to orgs (`org_id`, default `default`); ingest stamps the caller's org.
- Owner/admin can manage members (roles, with last-owner guard), teams (+members), invitations (self-serve accept creates user+membership at the invited role), and per-repo grants — all gated to org admins.
- A member sees/queries only repos granted to them or their teams (viewer read-only); owner/admin + agents see all org repos.
- **Non-breaking:** unconfigured/dev = open (existing behavior); the current single-org VM setup is a no-op (agent=default org, all repos=default org → full access as today).
- Server + lite both boot with the new tables; full pytest green; `ui` build green. Local login + OIDC + API tokens + MCP unchanged.

**This completes the 3-phase identity milestone.** Remaining manual: the user wires real OIDC client-IDs (Phase 2) + the VM deploy of all three phases (done together, with the user present, since Phase 1 adds the UI login wall and Phase 3 turns on repo enforcement).
