# Identity & Auth — Phase 1: Identity Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship durable organizations + human users with local (password) login, server-side sessions, a request-scoped `Principal`, and authentication enforcement on the API — working in both server (Postgres) and lite (SQLite) modes.

**Architecture:** New identity tables behind Protocols, each with a Postgres impl (`storage/`) and a lite-SQLite impl (`storage/lite/`), mirroring the existing `api_token_repo` pattern. A `Principal` dataclass is resolved per-request by a `get_principal` dependency that accepts a session cookie (humans), an API token (agents), or the legacy MCP key (back-compat). Auth endpoints live in a new `apps/api/routers/auth.py`. UI gains a login page + auth guard.

**Tech Stack:** FastAPI, SQLAlchemy async, argon2-cffi (password hashing), itsdangerous (signed/CSRF), Next.js (UI). Spec: `docs/superpowers/specs/2026-06-13-identity-auth-design.md`.

**Scope boundary:** Phase 1 = single bootstrap org, local auth only, authentication enforcement. Federation (OIDC/OAuth) is Phase 2. Teams / per-repo grants / invitations are Phase 3. In Phase 1, any authenticated member of the org may use the org's repos (mirrors current single-tenant behavior); fine-grained grants arrive in Phase 3.

---

## Conventions (read once)

- **Lite parity is mandatory.** Every table added in `storage/<x>_repo.py` (Postgres) gets a mirror in `storage/lite/<x>_repo.py` (SQLite). Postgres uses `TIMESTAMPTZ`/`BOOLEAN`; lite uses `TEXT` (ISO-8601) / `INTEGER 0|1`. Follow `storage/app_config_repo.py` ↔ `storage/lite/app_config_repo.py` exactly.
- **Schema bootstrap.** Each repo exposes `async def ensure_schema(self) -> None`. New repos are `ensure_schema()`'d in `apps/api/lifespan.py` (both `lifespan()` server path and `_build_lite_state`).
- **IDs.** Use `secrets.token_urlsafe(12)` for `user_id`/`org_id`/`session_id`/`membership_id` unless noted (match `api_token_repo` style).
- **Secrets never logged.** Password hashes and session tokens are sensitive; never put them in log lines or `repr`.
- **Tests run offline.** No network, no real Postgres in unit tests — use the SQLite-lite repos against a temp file engine for repo tests (this is how lite repos are already tested: see `tests/test_lite_config_repos.py`).
- Run the full suite with: `python -m pytest -q`. Run one file with `python -m pytest tests/<file> -q`.

---

## File Structure

**Create:**
- `core/auth/__init__.py` — exports
- `core/auth/passwords.py` — argon2id hash/verify
- `core/auth/principal.py` — `Principal` dataclass + role constants
- `core/auth/session_cache.py` — `SessionCache` (in-memory active session index)
- `storage/org_repo.py` — `OrgRow`, `PostgresOrgRepository`
- `storage/user_repo.py` — `UserRow`, `LocalCredentialRow`, `PostgresUserRepository`
- `storage/membership_repo.py` — `MembershipRow`, `PostgresMembershipRepository`
- `storage/session_repo.py` — `SessionRow`, `PostgresSessionRepository`
- `storage/lite/org_repo.py` — `SqliteOrgRepository`
- `storage/lite/user_repo.py` — `SqliteUserRepository`
- `storage/lite/membership_repo.py` — `SqliteMembershipRepository`
- `storage/lite/session_repo.py` — `SqliteSessionRepository`
- `apps/api/routers/auth.py` — register/login/logout/me + bootstrap
- `apps/api/auth_deps.py` — `get_principal`, `require_principal`, cookie helpers
- `schemas/auth.py` — request/response Pydantic models
- `ui/app/login/page.tsx` — login/register page
- `ui/lib/auth.ts` — client auth helpers
- Test files (one per task, paths in each task)

**Modify:**
- `pyproject.toml` / `requirements.lock.txt` — add `argon2-cffi`, `itsdangerous`
- `core/config.py` — add `session_ttl_seconds`
- `storage/repositories.py` — add Protocols for the four new repos
- `apps/api/state.py` — add the new repos to `AppState`
- `apps/api/lifespan.py` — construct + `ensure_schema` + seed default org (server + lite)
- `apps/api/dependencies.py` — getters for the new repos + `SessionCache`
- `apps/api/main.py` — include the `auth` router
- `core/auth/principal.py` consumers in routers (light enforcement) — Task 12
- `ui/app/layout.tsx` / a guard — redirect unauthenticated to `/login`
- `ui/lib/types.ts` — `UserView`, `PrincipalView`

---

## Task 1: Password hashing (argon2id)

**Files:**
- Modify: `pyproject.toml` (add `argon2-cffi>=23.1`, `itsdangerous>=2.2`)
- Create: `core/auth/__init__.py`, `core/auth/passwords.py`
- Test: `tests/test_auth_passwords.py`

- [ ] **Step 1: Add deps.** In `pyproject.toml` `dependencies`, add `"argon2-cffi>=23.1"` and `"itsdangerous>=2.2"`. Then `pip install argon2-cffi itsdangerous` and add pinned lines to `requirements.lock.txt` (`pip show argon2-cffi itsdangerous` for exact versions).

- [ ] **Step 2: Write the failing test.**

```python
# tests/test_auth_passwords.py
from core.auth.passwords import hash_password, verify_password

def test_hash_is_not_plaintext_and_verifies():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert h.startswith("$argon2")
    assert verify_password("correct horse battery staple", h) is True

def test_wrong_password_fails():
    h = hash_password("s3cret")
    assert verify_password("nope", h) is False

def test_two_hashes_differ_by_salt():
    assert hash_password("same") != hash_password("same")
```

- [ ] **Step 3: Run it, expect failure** (`ModuleNotFoundError`): `python -m pytest tests/test_auth_passwords.py -q`

- [ ] **Step 4: Implement.**

```python
# core/auth/passwords.py
"""argon2id password hashing. No determinism contract (random salt)."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_ph = PasswordHasher()  # argon2id defaults

def hash_password(plain: str) -> str:
    return _ph.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
```

```python
# core/auth/__init__.py
from core.auth.passwords import hash_password, verify_password

__all__ = ["hash_password", "verify_password"]
```

- [ ] **Step 5: Run, expect pass.** `python -m pytest tests/test_auth_passwords.py -q`

- [ ] **Step 6: Commit.** `git add core/auth pyproject.toml requirements.lock.txt tests/test_auth_passwords.py && git commit -m "feat(auth): argon2id password hashing"`

---

## Task 2: Principal + role constants

**Files:**
- Create: `core/auth/principal.py`
- Modify: `core/auth/__init__.py`
- Test: `tests/test_auth_principal.py`

- [ ] **Step 1: Failing test.**

```python
# tests/test_auth_principal.py
from core.auth.principal import Principal, ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER, ORG_ROLES

def test_agent_principal_factory():
    p = Principal.agent(org_id="default")
    assert p.kind == "agent" and p.is_authenticated and p.org_id == "default"
    assert "agent" in p.roles

def test_user_principal_has_role_and_org():
    p = Principal(kind="user", user_id="u1", org_id="acme", email="a@b.c",
                  roles=("admin",), is_authenticated=True)
    assert p.has_role(ROLE_ADMIN) and not p.has_role(ROLE_OWNER)

def test_org_roles_constant():
    assert ORG_ROLES == (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER)
```

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_auth_principal.py -q`

- [ ] **Step 3: Implement.**

```python
# core/auth/principal.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLE_VIEWER = "viewer"
ORG_ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER)

@dataclass(frozen=True, slots=True)
class Principal:
    kind: Literal["user", "agent"]
    user_id: str
    org_id: str
    email: str
    roles: tuple[str, ...] = field(default_factory=tuple)
    is_authenticated: bool = False

    def has_role(self, role: str) -> bool:
        return role in self.roles

    @classmethod
    def agent(cls, org_id: str) -> "Principal":
        return cls(kind="agent", user_id="agent", org_id=org_id,
                   email="", roles=("agent",), is_authenticated=True)

    @classmethod
    def anonymous(cls) -> "Principal":
        return cls(kind="user", user_id="", org_id="", email="",
                   roles=(), is_authenticated=False)
```

Add to `core/auth/__init__.py`: `from core.auth.principal import Principal, ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER, ROLE_VIEWER, ORG_ROLES` and extend `__all__`.

- [ ] **Step 4: Run, expect pass.** `python -m pytest tests/test_auth_principal.py -q`

- [ ] **Step 5: Commit.** `git commit -am "feat(auth): Principal + org role constants"`

---

## Task 3: Settings — session TTL

**Files:**
- Modify: `core/config.py`
- Test: `tests/test_auth_settings.py`

- [ ] **Step 1: Failing test.**

```python
# tests/test_auth_settings.py
from core.config import Settings

def test_session_ttl_default():
    s = Settings()
    assert s.session_ttl_seconds == 86400

def test_session_ttl_override(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_SECONDS", "3600")
    assert Settings().session_ttl_seconds == 3600
```

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_auth_settings.py -q`

- [ ] **Step 3: Implement.** In `core/config.py`, add alongside the other MCP/session settings (search for `mcp_session_ttl_seconds`):

```python
    session_ttl_seconds: int = 86400  # human browser session lifetime
```

- [ ] **Step 4: Run, expect pass; then full suite to ensure no Settings validation regressions.** `python -m pytest tests/test_auth_settings.py -q && python -m pytest tests/ -q -k config`

- [ ] **Step 5: Commit.** `git commit -am "feat(config): SESSION_TTL_SECONDS setting"`

---

## Task 4: Org repository (Postgres + lite) + Protocol

**Files:**
- Modify: `storage/repositories.py` (add `OrgRepository` Protocol)
- Create: `storage/org_repo.py`, `storage/lite/org_repo.py`
- Test: `tests/test_org_repo.py`

- [ ] **Step 1: Failing test** (runs against the lite SQLite repo with a temp engine — same approach as `tests/test_lite_config_repos.py`; read that file first for the engine fixture pattern).

```python
# tests/test_org_repo.py
import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.org_repo import SqliteOrgRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "t.db"))
    r = SqliteOrgRepository(engine)
    await r.ensure_schema()
    return r

async def test_create_and_get_org(repo):
    row = await repo.create_org(org_id="acme", name="Acme", slug="acme")
    assert row.org_id == "acme" and row.slug == "acme"
    got = await repo.get_org("acme")
    assert got is not None and got.name == "Acme"

async def test_get_by_slug_and_list(repo):
    await repo.create_org(org_id="o1", name="One", slug="one")
    assert (await repo.get_org_by_slug("one")).org_id == "o1"
    assert len(await repo.list_orgs()) == 1

async def test_default_org_idempotent(repo):
    a = await repo.ensure_default_org()
    b = await repo.ensure_default_org()
    assert a.org_id == b.org_id  # same row, no duplicate
    assert len(await repo.list_orgs()) == 1
```

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_org_repo.py -q`

- [ ] **Step 3: Add the Protocol** to `storage/repositories.py`:

```python
@runtime_checkable
class OrgRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def create_org(self, *, org_id: str, name: str, slug: str) -> "OrgRow": ...
    async def get_org(self, org_id: str) -> "OrgRow | None": ...
    async def get_org_by_slug(self, slug: str) -> "OrgRow | None": ...
    async def list_orgs(self) -> "list[OrgRow]": ...
    async def ensure_default_org(self) -> "OrgRow": ...
```

(Import `OrgRow` under `TYPE_CHECKING` from `storage.org_repo` to avoid a cycle, matching how other rows are referenced.)

- [ ] **Step 4: Implement Postgres** `storage/org_repo.py`. Model it on `storage/app_config_repo.py` (engine handling, `ensure_schema`, row dataclass, `text()` SQL). `DEFAULT_ORG_ID = "default"`.

```python
# storage/org_repo.py  (sketch — follow app_config_repo.py for engine/text patterns)
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

DEFAULT_ORG_ID = "default"
DEFAULT_ORG_SLUG = "default"

@dataclass(frozen=True, slots=True)
class OrgRow:
    org_id: str
    name: str
    slug: str
    created_at: datetime | None = None

_DDL = """
CREATE TABLE IF NOT EXISTS organizations (
    org_id     TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

class PostgresOrgRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL))

    async def create_org(self, *, org_id: str, name: str, slug: str) -> OrgRow:
        async with self._engine.begin() as conn:
            await conn.execute(text(
                "INSERT INTO organizations (org_id, name, slug) VALUES (:i, :n, :s) "
                "ON CONFLICT (org_id) DO NOTHING"), {"i": org_id, "n": name, "s": slug})
        got = await self.get_org(org_id)
        assert got is not None
        return got

    async def get_org(self, org_id: str) -> OrgRow | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT org_id, name, slug, created_at FROM organizations WHERE org_id=:i"),
                {"i": org_id})).mappings().first()
        return OrgRow(**row) if row else None

    async def get_org_by_slug(self, slug: str) -> OrgRow | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(text(
                "SELECT org_id, name, slug, created_at FROM organizations WHERE slug=:s"),
                {"s": slug})).mappings().first()
        return OrgRow(**row) if row else None

    async def list_orgs(self) -> list[OrgRow]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT org_id, name, slug, created_at FROM organizations ORDER BY created_at"))).mappings().all()
        return [OrgRow(**r) for r in rows]

    async def ensure_default_org(self) -> OrgRow:
        existing = await self.get_org(DEFAULT_ORG_ID)
        if existing is not None:
            return existing
        return await self.create_org(org_id=DEFAULT_ORG_ID, name="Default", slug=DEFAULT_ORG_SLUG)
```

- [ ] **Step 5: Implement lite** `storage/lite/org_repo.py`. Same class surface; SQLite DDL: `created_at TEXT NOT NULL DEFAULT (datetime('now'))`, no `TIMESTAMPTZ`. Parse `created_at` from ISO `TEXT` to `datetime` in `_row()` (follow `storage/lite/app_config_repo.py` `_ts`/hydration helpers). `ON CONFLICT(org_id) DO NOTHING` works in SQLite ≥ 3.24.

- [ ] **Step 6: Export** both from `storage/__init__.py` and `storage/lite/__init__.py` if those modules re-export (check first; match neighbors).

- [ ] **Step 7: Run, expect pass.** `python -m pytest tests/test_org_repo.py -q`

- [ ] **Step 8: Commit.** `git add -A && git commit -m "feat(storage): durable organizations repo (pg + lite)"`

---

## Task 5: User repository + local credentials (Postgres + lite)

**Files:**
- Modify: `storage/repositories.py` (`UserRepository` Protocol)
- Create: `storage/user_repo.py`, `storage/lite/user_repo.py`
- Test: `tests/test_user_repo.py`

`users` table: `user_id PK, email UNIQUE (lowercased), display_name, avatar_url, status, created_at`.
`local_credentials` table: `user_id PK/FK, password_hash, updated_at`. (Federated identities are Phase 2.)

- [ ] **Step 1: Failing test** (lite engine fixture as in Task 4).

```python
# tests/test_user_repo.py
import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.user_repo import SqliteUserRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "u.db"))
    r = SqliteUserRepository(engine)
    await r.ensure_schema()
    return r

async def test_create_user_and_get_by_email_is_case_insensitive(repo):
    u = await repo.create_user(user_id="u1", email="A@Example.com", display_name="A")
    assert u.email == "a@example.com"
    assert (await repo.get_by_email("a@example.COM")).user_id == "u1"

async def test_set_and_get_password_hash(repo):
    await repo.create_user(user_id="u1", email="a@b.c", display_name="A")
    await repo.set_password(user_id="u1", password_hash="$argon2id$xxx")
    assert await repo.get_password_hash("u1") == "$argon2id$xxx"

async def test_count_users(repo):
    assert await repo.count_users() == 0
    await repo.create_user(user_id="u1", email="a@b.c", display_name="A")
    assert await repo.count_users() == 1

async def test_duplicate_email_rejected(repo):
    await repo.create_user(user_id="u1", email="a@b.c", display_name="A")
    with pytest.raises(Exception):
        await repo.create_user(user_id="u2", email="A@B.C", display_name="B")
```

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_user_repo.py -q`

- [ ] **Step 3: Protocol** in `storage/repositories.py`:

```python
@runtime_checkable
class UserRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def create_user(self, *, user_id: str, email: str, display_name: str,
                          avatar_url: str = "") -> "UserRow": ...
    async def get_user(self, user_id: str) -> "UserRow | None": ...
    async def get_by_email(self, email: str) -> "UserRow | None": ...
    async def count_users(self) -> int: ...
    async def set_password(self, *, user_id: str, password_hash: str) -> None: ...
    async def get_password_hash(self, user_id: str) -> "str | None": ...
```

- [ ] **Step 4: Implement Postgres** `storage/user_repo.py`. `UserRow(user_id, email, display_name, avatar_url, status, created_at)`. Normalize email with `email.strip().lower()` in `create_user`/`get_by_email`. Two tables in `ensure_schema` (`users`, `local_credentials`). `set_password` upserts `local_credentials` (`ON CONFLICT (user_id) DO UPDATE SET password_hash=...`). Let the UNIQUE(email) constraint raise on duplicates.

- [ ] **Step 5: Implement lite** `storage/lite/user_repo.py` — same surface, SQLite types, ISO timestamps.

- [ ] **Step 6: Run, expect pass.** `python -m pytest tests/test_user_repo.py -q`

- [ ] **Step 7: Commit.** `git add -A && git commit -m "feat(storage): users + local credentials repo (pg + lite)"`

---

## Task 6: Membership repository (Postgres + lite)

**Files:**
- Modify: `storage/repositories.py` (`MembershipRepository` Protocol)
- Create: `storage/membership_repo.py`, `storage/lite/membership_repo.py`
- Test: `tests/test_membership_repo.py`

`memberships` table: `membership_id PK, user_id, org_id, role, status, created_at, UNIQUE(user_id, org_id)`.

- [ ] **Step 1: Failing test.**

```python
# tests/test_membership_repo.py
import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.membership_repo import SqliteMembershipRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "m.db"))
    r = SqliteMembershipRepository(engine)
    await r.ensure_schema()
    return r

async def test_add_and_get_membership(repo):
    m = await repo.add_member(membership_id="m1", user_id="u1", org_id="acme", role="owner")
    assert m.role == "owner"
    got = await repo.get_membership(user_id="u1", org_id="acme")
    assert got.role == "owner"

async def test_list_orgs_for_user(repo):
    await repo.add_member(membership_id="m1", user_id="u1", org_id="acme", role="owner")
    await repo.add_member(membership_id="m2", user_id="u1", org_id="beta", role="member")
    orgs = await repo.list_orgs_for_user("u1")
    assert {o.org_id for o in orgs} == {"acme", "beta"}

async def test_list_members_of_org(repo):
    await repo.add_member(membership_id="m1", user_id="u1", org_id="acme", role="owner")
    await repo.add_member(membership_id="m2", user_id="u2", org_id="acme", role="member")
    assert len(await repo.list_members(org_id="acme")) == 2

async def test_set_role(repo):
    await repo.add_member(membership_id="m1", user_id="u1", org_id="acme", role="member")
    await repo.set_role(user_id="u1", org_id="acme", role="admin")
    assert (await repo.get_membership(user_id="u1", org_id="acme")).role == "admin"
```

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_membership_repo.py -q`

- [ ] **Step 3: Protocol.**

```python
@runtime_checkable
class MembershipRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def add_member(self, *, membership_id: str, user_id: str, org_id: str,
                         role: str, status: str = "active") -> "MembershipRow": ...
    async def get_membership(self, *, user_id: str, org_id: str) -> "MembershipRow | None": ...
    async def list_orgs_for_user(self, user_id: str) -> "list[MembershipRow]": ...
    async def list_members(self, *, org_id: str) -> "list[MembershipRow]": ...
    async def set_role(self, *, user_id: str, org_id: str, role: str) -> None: ...
```

- [ ] **Step 4: Implement Postgres** `storage/membership_repo.py`. `MembershipRow(membership_id, user_id, org_id, role, status, created_at)`. `UNIQUE(user_id, org_id)`.

- [ ] **Step 5: Implement lite** `storage/lite/membership_repo.py`.

- [ ] **Step 6: Run, expect pass.** `python -m pytest tests/test_membership_repo.py -q`

- [ ] **Step 7: Commit.** `git add -A && git commit -m "feat(storage): membership repo (pg + lite)"`

---

## Task 7: Session repository (Postgres + lite)

**Files:**
- Modify: `storage/repositories.py` (`SessionRepository` Protocol)
- Create: `storage/session_repo.py`, `storage/lite/session_repo.py`
- Test: `tests/test_session_repo.py`

`sessions` table: `session_id PK (sha256 of raw cookie), user_id, active_org_id, csrf_token, created_at, expires_at, revoked_at`. **Store only the hash of the cookie value** (like API tokens). `create_session` takes the already-hashed id.

- [ ] **Step 1: Failing test.**

```python
# tests/test_session_repo.py
import pytest
from datetime import datetime, timezone, timedelta
from storage.lite.engine import make_sqlite_engine
from storage.lite.session_repo import SqliteSessionRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "s.db"))
    r = SqliteSessionRepository(engine)
    await r.ensure_schema()
    return r

def _exp(secs): return datetime.now(timezone.utc) + timedelta(seconds=secs)

async def test_create_and_get_active(repo):
    await repo.create_session(session_id="h1", user_id="u1", active_org_id="acme",
                              csrf_token="c1", expires_at=_exp(3600))
    s = await repo.get_active("h1")
    assert s is not None and s.user_id == "u1" and s.active_org_id == "acme"

async def test_expired_not_returned(repo):
    await repo.create_session(session_id="h2", user_id="u1", active_org_id="acme",
                              csrf_token="c", expires_at=_exp(-1))
    assert await repo.get_active("h2") is None

async def test_revoke(repo):
    await repo.create_session(session_id="h3", user_id="u1", active_org_id="acme",
                              csrf_token="c", expires_at=_exp(3600))
    await repo.revoke("h3")
    assert await repo.get_active("h3") is None

async def test_list_active_hashes(repo):
    await repo.create_session(session_id="h4", user_id="u1", active_org_id="acme",
                              csrf_token="c", expires_at=_exp(3600))
    await repo.create_session(session_id="h5", user_id="u1", active_org_id="acme",
                              csrf_token="c", expires_at=_exp(-1))
    active = await repo.list_active_session_ids()
    assert "h4" in active and "h5" not in active
```

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_session_repo.py -q`

- [ ] **Step 3: Protocol.**

```python
@runtime_checkable
class SessionRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def create_session(self, *, session_id: str, user_id: str, active_org_id: str,
                             csrf_token: str, expires_at: datetime) -> "SessionRow": ...
    async def get_active(self, session_id: str) -> "SessionRow | None": ...
    async def revoke(self, session_id: str) -> None: ...
    async def list_active_session_ids(self) -> "set[str]": ...
```

- [ ] **Step 4: Implement Postgres** `storage/session_repo.py`. `get_active` filters `revoked_at IS NULL AND expires_at > now()`. For lite, compare ISO strings carefully — store `expires_at` as ISO UTC and compare in Python after fetch (fetch row, check expiry in code) to avoid string-compare pitfalls; OR store epoch INTEGER. **Use epoch INTEGER for `expires_at`/`created_at` in lite** to make comparisons trivial and correct.

- [ ] **Step 5: Implement lite** `storage/lite/session_repo.py` (epoch INTEGER columns; convert to/from `datetime` at the boundary).

- [ ] **Step 6: Run, expect pass.** `python -m pytest tests/test_session_repo.py -q`

- [ ] **Step 7: Commit.** `git add -A && git commit -m "feat(storage): session repo (pg + lite)"`

---

## Task 8: SessionCache

**Files:**
- Create: `core/auth/session_cache.py`
- Modify: `core/auth/__init__.py`
- Test: `tests/test_session_cache.py`

Mirrors `core/token_cache.py`: holds the set of active session-id hashes for O(1) sync `is_valid`, refreshed from the repo.

- [ ] **Step 1: Failing test.**

```python
# tests/test_session_cache.py
import pytest
from core.auth.session_cache import SessionCache

class _FakeRepo:
    def __init__(self, ids): self._ids = set(ids)
    async def list_active_session_ids(self): return set(self._ids)
    def drop(self, i): self._ids.discard(i)

async def test_refresh_then_valid():
    repo = _FakeRepo({"a", "b"})
    cache = SessionCache(repo)  # type: ignore[arg-type]
    await cache.refresh()
    assert cache.is_valid("a") and not cache.is_valid("z")
    assert cache.active_count() == 2

async def test_add_and_invalidate_sync():
    repo = _FakeRepo(set())
    cache = SessionCache(repo)  # type: ignore[arg-type]
    cache.add("x")
    assert cache.is_valid("x")
    cache.invalidate("x")
    assert not cache.is_valid("x")
```

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_session_cache.py -q`

- [ ] **Step 3: Implement** (copy the shape of `core/token_cache.py`):

```python
# core/auth/session_cache.py
from __future__ import annotations
from typing import Protocol

class _Repo(Protocol):
    async def list_active_session_ids(self) -> set[str]: ...

class SessionCache:
    def __init__(self, repo: _Repo) -> None:
        self.repo = repo
        self._active: set[str] = set()

    async def refresh(self) -> None:
        self._active = await self.repo.list_active_session_ids()

    def is_valid(self, session_id: str) -> bool:
        return session_id in self._active

    def add(self, session_id: str) -> None:
        self._active.add(session_id)

    def invalidate(self, session_id: str) -> None:
        self._active.discard(session_id)

    def active_count(self) -> int:
        return len(self._active)
```

Add to `core/auth/__init__.py`.

- [ ] **Step 4: Run, expect pass.** `python -m pytest tests/test_session_cache.py -q`

- [ ] **Step 5: Commit.** `git commit -am "feat(auth): SessionCache (O(1) active-session index)"`

---

## Task 9: Wire repos into AppState + lifespan (server + lite) + seed default org

**Files:**
- Modify: `apps/api/state.py`, `apps/api/lifespan.py`, `apps/api/dependencies.py`
- Test: `tests/test_auth_lifespan_wiring.py`

- [ ] **Step 1: Failing test** — boot lite app, assert the new repos + default org + SessionCache exist on `app.state`.

```python
# tests/test_auth_lifespan_wiring.py
import os, pytest
from httpx import AsyncClient, ASGITransport

@pytest.fixture
async def lite_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "lite")
    monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path))
    from core.config import get_settings
    get_settings.cache_clear()
    from apps.api.main import app
    async with app.router.lifespan_context(app):
        yield app

async def test_default_org_seeded_and_caches_present(lite_app):
    st = lite_app.state
    assert st.session_cache is not None
    org = await st.app_state.org_repo.get_org("default")
    assert org is not None and org.slug == "default"
```

(Confirm the exact AppState access pattern from `tests/test_lite_app_boot.py` first; adapt attribute names if needed.)

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_auth_lifespan_wiring.py -q`

- [ ] **Step 3: Add fields to `AppState`** (`apps/api/state.py`): `org_repo`, `user_repo`, `membership_repo`, `session_repo`. Keep `with_default_embedder` working (add params with defaults or thread through; match existing signature style).

- [ ] **Step 4: Server path** in `apps/api/lifespan.py` `lifespan()`: construct `PostgresOrgRepository(engine)` etc. (reuse the same Postgres engine the other repos use), `await *.ensure_schema()` for all four, `await org_repo.ensure_default_org()`, build `SessionCache(session_repo)`, `await session_cache.refresh()`, attach `app.state.session_cache = session_cache`.

- [ ] **Step 5: Lite path** `_build_lite_state`: construct the `Sqlite*` repos on the shared lite engine, ensure_schema, ensure_default_org, build + refresh SessionCache. Return them via the AppState (and the tuple if needed; mirror how `token_cache` is threaded).

- [ ] **Step 6: Dependencies** (`apps/api/dependencies.py`): add `get_org_repo`, `get_user_repo`, `get_membership_repo`, `get_session_repo`, `get_session_cache` + `Annotated` type aliases (mirror `get_token_cache`).

- [ ] **Step 7: Run, expect pass; then full suite.** `python -m pytest tests/test_auth_lifespan_wiring.py -q && python -m pytest tests/ -q`

- [ ] **Step 8: Commit.** `git add -A && git commit -m "feat(api): wire identity repos + SessionCache + default org into lifespan"`

---

## Task 10: Auth schemas + cookie/session helpers

**Files:**
- Create: `schemas/auth.py`, `apps/api/auth_deps.py`
- Test: `tests/test_auth_deps.py`

`apps/api/auth_deps.py` holds: cookie name constant (`COOKIE_NAME = "memcl_session"`), `hash_session_token(raw)` (sha256), `new_session_token()` (`secrets.token_urlsafe(32)`), `set_session_cookie(response, raw, ttl)` / `clear_session_cookie(response)` (httpOnly, secure-from-settings, samesite=lax, path=/), and the `get_principal` / `require_principal` dependencies (Task 11 fills the resolution body — here just the helpers + schemas).

- [ ] **Step 1: Failing test.**

```python
# tests/test_auth_deps.py
from apps.api.auth_deps import hash_session_token, new_session_token, COOKIE_NAME

def test_hash_is_deterministic_and_hex():
    t = "raw-token"
    assert hash_session_token(t) == hash_session_token(t)
    assert len(hash_session_token(t)) == 64

def test_new_token_unique():
    assert new_session_token() != new_session_token()

def test_cookie_name():
    assert COOKIE_NAME == "memcl_session"
```

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_auth_deps.py -q`

- [ ] **Step 3: Implement** `schemas/auth.py` (Pydantic, `extra="forbid"` to match house style):

```python
# schemas/auth.py
from pydantic import BaseModel, EmailStr, Field

class RegisterRequest(BaseModel):
    model_config = {"extra": "forbid"}
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1, max_length=200)

class LoginRequest(BaseModel):
    model_config = {"extra": "forbid"}
    email: EmailStr
    password: str

class UserView(BaseModel):
    user_id: str
    email: str
    display_name: str
    org_id: str
    roles: list[str]

class MeResponse(BaseModel):
    authenticated: bool
    user: UserView | None = None
```

(If `EmailStr` requires `email-validator`, add `pydantic[email]` to deps in this task and to the lock file.)

And the helpers in `apps/api/auth_deps.py`:

```python
# apps/api/auth_deps.py (helpers portion)
from __future__ import annotations
import hashlib, secrets
from fastapi import Response
from core.config import get_settings

COOKIE_NAME = "memcl_session"

def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def new_session_token() -> str:
    return secrets.token_urlsafe(32)

def set_session_cookie(response: Response, raw: str, ttl_seconds: int) -> None:
    secure = get_settings().environment == "production"
    response.set_cookie(COOKIE_NAME, raw, max_age=ttl_seconds, httponly=True,
                        secure=secure, samesite="lax", path="/")

def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
```

- [ ] **Step 4: Run, expect pass.** `python -m pytest tests/test_auth_deps.py -q`

- [ ] **Step 5: Commit.** `git add -A && git commit -m "feat(auth): auth schemas + cookie/session helpers"`

---

## Task 11: `get_principal` dependency (cookie → token → legacy key)

**Files:**
- Modify: `apps/api/auth_deps.py`
- Test: `tests/test_get_principal.py`

Resolution order: valid session cookie → user `Principal` (look up membership for `active_org_id` → roles). Else an accepted API token / legacy key (reuse `credential_accepted`) → `Principal.agent(default_org)`. Else `Principal.anonymous()`. `require_principal` raises 401 if not authenticated.

- [ ] **Step 1: Failing test** (uses the lite app + a created user/session; build a helper that registers via the repos directly).

```python
# tests/test_get_principal.py
import pytest
from httpx import AsyncClient, ASGITransport

@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "lite")
    monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path))
    from core.config import get_settings
    get_settings.cache_clear()
    from apps.api.main import app
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c, app

async def test_me_anonymous(client):
    c, _ = client
    r = await c.get("/auth/me")
    assert r.status_code == 200 and r.json()["authenticated"] is False

async def test_register_then_me_authenticated(client):
    c, _ = client
    r = await c.post("/auth/register", json={"email": "a@b.c", "password": "password123", "display_name": "A"})
    assert r.status_code == 200
    me = await c.get("/auth/me")
    body = me.json()
    assert body["authenticated"] is True and body["user"]["email"] == "a@b.c"
    assert body["user"]["roles"] == ["owner"]  # first user
```

(This test also exercises Task 12's router — it's fine for the test to span both; implement Task 12 to make it pass, then keep it green.)

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_get_principal.py -q`

- [ ] **Step 3: Implement `get_principal`** in `apps/api/auth_deps.py`:

```python
from fastapi import Depends, HTTPException, Request
from typing import Annotated
from core.auth.principal import Principal
from apps.api.dependencies import (
    SessionRepoDep, MembershipRepoDep, SessionCacheDep,
)
from apps.mcp.auth import require_mcp_api_key  # returns presented key or None
from storage.org_repo import DEFAULT_ORG_ID

async def get_principal(
    request: Request,
    session_repo: SessionRepoDep,
    membership_repo: MembershipRepoDep,
    session_cache: SessionCacheDep,
    api_key: Annotated[str | None, Depends(require_mcp_api_key)] = None,
) -> Principal:
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        sid = hash_session_token(raw)
        if session_cache.is_valid(sid):
            sess = await session_repo.get_active(sid)
            if sess is not None:
                m = await membership_repo.get_membership(
                    user_id=sess.user_id, org_id=sess.active_org_id)
                roles = (m.role,) if m else ()
                # email/display fetched lazily where needed; keep Principal light
                return Principal(kind="user", user_id=sess.user_id,
                                 org_id=sess.active_org_id, email="",
                                 roles=roles, is_authenticated=True)
    if api_key is not None:
        return Principal.agent(org_id=DEFAULT_ORG_ID)
    return Principal.anonymous()

async def require_principal(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    if not principal.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal

PrincipalDep = Annotated[Principal, Depends(get_principal)]
RequirePrincipalDep = Annotated[Principal, Depends(require_principal)]
```

> Note: `require_mcp_api_key` raises 401 when a key is configured but absent. For `get_principal` we want it to be permissive (cookie path may still apply). Wrap the api-key resolution in a try/except or add a non-raising variant `resolve_presented_key(request)` in `apps/mcp/auth.py` and use that here. Implementer: prefer adding `resolve_presented_key` (pure, non-raising) and call it directly instead of `Depends(require_mcp_api_key)`.

- [ ] **Step 4: Run** (will pass after Task 12). For now: `python -m pytest tests/test_get_principal.py::test_me_anonymous -q` once the router exists; full pass at end of Task 12.

- [ ] **Step 5: Commit.** `git add -A && git commit -m "feat(auth): get_principal dependency (cookie/token/legacy resolution)"`

---

## Task 12: Auth router (register / login / logout / me) + bootstrap

**Files:**
- Create: `apps/api/routers/auth.py`
- Modify: `apps/api/main.py` (include router), `apps/mcp/auth.py` (add `resolve_presented_key`)
- Test: completes `tests/test_get_principal.py` + `tests/test_auth_router.py`

Bootstrap rule: if `user_repo.count_users() == 0`, `register` is open and the first user becomes **owner** of the default org. Otherwise `register` requires an authenticated **admin/owner** (invitations come in Phase 3 — until then, only admins can add users).

- [ ] **Step 1: Failing test.**

```python
# tests/test_auth_router.py  (reuse the lite client fixture from test_get_principal.py via conftest or inline)
import pytest
from httpx import AsyncClient, ASGITransport

@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "lite"); monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path))
    from core.config import get_settings; get_settings.cache_clear()
    from apps.api.main import app
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c

async def test_login_logout_cycle(client):
    await client.post("/auth/register", json={"email": "a@b.c", "password": "password123", "display_name": "A"})
    # logout clears cookie
    await client.post("/auth/logout")
    assert (await client.get("/auth/me")).json()["authenticated"] is False
    # login again
    r = await client.post("/auth/login", json={"email": "a@b.c", "password": "password123"})
    assert r.status_code == 200
    assert (await client.get("/auth/me")).json()["authenticated"] is True

async def test_bad_password_rejected(client):
    await client.post("/auth/register", json={"email": "a@b.c", "password": "password123", "display_name": "A"})
    await client.post("/auth/logout")
    r = await client.post("/auth/login", json={"email": "a@b.c", "password": "WRONG"})
    assert r.status_code == 401

async def test_second_register_requires_admin(client):
    await client.post("/auth/register", json={"email": "a@b.c", "password": "password123", "display_name": "A"})
    await client.post("/auth/logout")
    # anonymous second registration is refused now that a user exists
    r = await client.post("/auth/register", json={"email": "x@y.z", "password": "password123", "display_name": "X"})
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: Run, expect fail.** `python -m pytest tests/test_auth_router.py -q`

- [ ] **Step 3: Implement** `apps/api/routers/auth.py`. On register (first user): create user, set password, create default-org membership `owner`, create session + set cookie + `session_cache.add(sid)`. On login: `get_by_email` → `verify_password(plain, get_password_hash)` → create session + cookie. On logout: revoke session + `session_cache.invalidate` + clear cookie. `GET /auth/me`: build `MeResponse` from `get_principal` (+ hydrate email/display from `user_repo`). Add `resolve_presented_key(request)` to `apps/mcp/auth.py` (non-raising helper used by `get_principal`). Include the router in `apps/api/main.py` (`app.include_router(auth.router)`).

- [ ] **Step 4: Run both test files, expect pass.** `python -m pytest tests/test_auth_router.py tests/test_get_principal.py -q`

- [ ] **Step 5: Full suite.** `python -m pytest tests/ -q`

- [ ] **Step 6: Commit.** `git add -A && git commit -m "feat(api): auth router — register/login/logout/me + first-user bootstrap"`

---

## Task 13: Authentication enforcement on the API

**Files:**
- Modify: routers that mutate/read sensitive data (start with `apps/api/routers/config.py` already partially gated; add principal-awareness where the MCP-key gate is today). Conservative scope: require an authenticated principal OR a valid API key for the existing protected surface; do not break the legacy key path.
- Test: `tests/test_auth_enforcement.py`

The goal of Phase 1 is **authentication** (who you are), not yet fine-grained per-repo authorization (Phase 3). Concretely: where `_require_bootstrap_or_authed` currently checks only the API key, also accept an authenticated session principal. Keep behavior identical when no users exist (dev/bootstrap) and when only the legacy key is used (agents).

- [ ] **Step 1: Failing test.**

```python
# tests/test_auth_enforcement.py
import pytest
from httpx import AsyncClient, ASGITransport

@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "lite"); monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path))
    from core.config import get_settings; get_settings.cache_clear()
    from apps.api.main import app
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            yield c

async def test_logged_in_user_can_reach_config_mutation(client):
    # first-user registration logs us in (cookie set)
    await client.post("/auth/register", json={"email": "a@b.c", "password": "password123", "display_name": "A"})
    # an authenticated session should satisfy bootstrap-or-authed even after a key is set
    r = await client.post("/config/openai-key", json={"api_key": "sk-test"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run, expect fail or pass** — characterize current behavior first (`-q`), then adjust `_require_bootstrap_or_authed` to also accept a session principal.

- [ ] **Step 3: Implement.** Thread `PrincipalDep` into `_require_bootstrap_or_authed` (or add a parallel check): authed if `runtime.configured()` is False (bootstrap) OR api_key present OR `principal.is_authenticated`.

- [ ] **Step 4: Run, expect pass; full suite.** `python -m pytest tests/test_auth_enforcement.py -q && python -m pytest tests/ -q`

- [ ] **Step 5: Commit.** `git add -A && git commit -m "feat(api): accept authenticated session for protected config surface"`

---

## Task 14: UI — login page + auth guard + current user

**Files:**
- Create: `ui/app/login/page.tsx`, `ui/lib/auth.ts`
- Modify: `ui/lib/types.ts` (`UserView`, `MeResponse`), `ui/app/layout.tsx` (or a client guard component), the existing nav/header to show user + logout
- Test: `ui/` production build must pass (`npm run build` in `ui/`); add a Playwright smoke if the repo has Playwright wired (check `ui/` for existing e2e setup before adding).

Match the existing **light/emerald** design system (see `reference_memcl_ui_dev_loop` patterns / existing `ui/app/settings/page.tsx`).

- [ ] **Step 1:** Add `MeResponse`/`UserView` to `ui/lib/types.ts` mirroring `schemas/auth.py`.
- [ ] **Step 2:** `ui/lib/auth.ts` — `fetchMe()`, `login(email,pw)`, `register(...)`, `logout()` calling `/auth/*` (cookies are same-origin; rely on the existing `/api` proxy/middleware — confirm whether auth routes need adding to the rewrite matcher in `ui/middleware.ts`).
- [ ] **Step 3:** `ui/app/login/page.tsx` — emerald-styled login + (conditional) first-user register form; on success route to `/`.
- [ ] **Step 4:** Guard — a small client component in `layout.tsx` that calls `fetchMe()`; if unauthenticated and not on `/login` or `/setup`, redirect to `/login`. Show the user's email + a logout button in the header.
- [ ] **Step 5: Build gate.** `cd ui && npm run build` — must succeed.
- [ ] **Step 6: Commit.** `git add -A && git commit -m "feat(ui): login page + auth guard + current-user header"`

---

## Task 15: Docs + final review

**Files:**
- Modify: `docs/22_SECURITY_AND_ACCESS_CONTROL.md` (add the human-identity section: orgs, users, roles, sessions, login flow — note Phase 2 federation + Phase 3 RBAC are coming), `docs/06_CONFIGURATION.md` (`SESSION_TTL_SECONDS`), `docs/26_GLOSSARY.md` (Organization, User, Membership, Session, Principal, Role).
- Test: full suite + lite boot.

- [ ] **Step 1:** Update the three docs (concise; mark federation/RBAC as Phase 2/3).
- [ ] **Step 2: Full gates.** `python -m pytest tests/ -q` (all green) and a real lite smoke: `MODE=lite LITE_DATA_DIR=/tmp/memcl-auth memcl serve` boots, `/auth/me` returns `authenticated:false`, register works, `/auth/me` returns the owner. (Do this manually or via the e2e test already added.)
- [ ] **Step 3: Commit.** `git add -A && git commit -m "docs: document Phase-1 identity (orgs, users, sessions, roles)"`

---

## Done criteria (Phase 1)

- Server **and** lite boot with the four new tables + a seeded default org.
- First user registers → becomes org **owner**, gets a session cookie, `/auth/me` reflects it.
- Login/logout cycle works; bad password → 401; second anonymous register refused.
- Legacy MCP key + API tokens still authenticate (agent Principal) — no regression in the existing suite.
- UI: unauthenticated users land on `/login`; authenticated users see their identity + can log out.
- Full pytest suite green; `ui` production build green.

**Next:** Phase 2 plan (federation: authlib OIDC/OAuth engine + `auth_providers` runtime config + GitHub/Google/Microsoft/generic presets + Settings provider UI) — written after Phase 1 merges.
