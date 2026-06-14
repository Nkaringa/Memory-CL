# Identity & Auth — Phase 2: Federation (OIDC/OAuth) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let humans log in via **GitHub / Google / Microsoft / generic OIDC** in addition to local password, configured at runtime (operator pastes client-id/secret into Settings, toggles a provider on — no code change, no restart). Built and tested now against a deterministic mock; real providers verified later when the operator supplies credentials.

**Architecture:** A runtime `auth_providers` table (multi-row, like `api_tokens`) holds each provider's type/client-id/secret/scopes/enabled. An `OAuthRegistry` builds an authlib `starlette_client.OAuth` from the *enabled* providers and rebuilds on change. The login flow (`/auth/oauth/{provider}/start` → provider → `/auth/oauth/{provider}/callback`) exchanges the code, resolves a **verified email + stable subject**, links to (or creates) a user via a new `federated_identities` table, then reuses Phase-1's `_create_session`. State/nonce/PKCE are carried in a dedicated SessionMiddleware signed cookie (handshake-only).

**Tech Stack:** authlib (OIDC/OAuth2 + PKCE), Starlette SessionMiddleware (handshake state), FastAPI, SQLAlchemy async (pg + lite SQLite), Next.js. Spec: `docs/superpowers/specs/2026-06-13-identity-auth-design.md`. Builds on Phase 1 (PR #37).

**Scope boundary:** Phase 2 = federated authentication + provider config + account-linking + login UI. Teams / invitations / per-repo grants remain Phase 3. Federated users join the default org as `member` (first-ever user becomes `owner`, same bootstrap rule as local).

---

## Conventions (read once — same as Phase 1)

- **Lite parity mandatory.** Every `storage/<x>_repo.py` (Postgres) gets a `storage/lite/<x>_repo.py` (SQLite) mirror. Postgres `TIMESTAMPTZ`/`BOOLEAN`; lite `TEXT` ISO / `INTEGER 0|1`. Follow `storage/api_token_repo.py` ↔ `storage/lite/api_token_repo.py` (multi-row analog) and the Phase-1 `org_repo`/`user_repo` pairs.
- **No bare `assert`** for storage invariants → `if x is None: raise RuntimeError(...)`. `from __future__ import annotations`; `@dataclass(frozen=True, slots=True)`; `created_at/updated_at: datetime | None = None`.
- **Secrets:** `client_secret` is stored plaintext in the row (same trust model as `app_config` keys — admin-only access) but **never returned** by any GET (mask to a hint). Never logged.
- **Tests run offline & deterministic.** No real provider, no port binding. Mock the authlib client's token-exchange/userinfo at the method boundary (see Task 8). Use the Phase-1 lite async client fixture (`tests/test_auth_router.py`).
- Full suite: `python -m pytest -q`. UI build gate: `cd ui && npm run build`.
- **Commit message rule:** no AI/Claude attribution. Single-line shell commands.

---

## Architecture decisions (locked)

1. **Handshake state store = Starlette `SessionMiddleware`** (a dedicated signed cookie `memcl_oauth`, `max_age` ~300s, httponly, samesite=lax). authlib's starlette client reads/writes state+nonce+code_verifier there. This is separate from the Phase-1 `memcl_session` identity cookie and carries **no identity** — only transient handshake data. Secret from a new `OAUTH_STATE_SECRET` setting (falls back to a derived value if unset, but warn). Rationale: documented authlib happy-path, no authlib-internals monkeypatching, testable.
2. **`auth_providers` = new multi-row table** (NOT app_config/RuntimeConfig, which is single-row). An `OAuthRegistry` on `app.state` is (re)built from enabled providers at boot and after any mutation.
3. **PKCE** (`code_challenge_method="S256"`) on every provider authlib supports it for. **Account-linking only on a `verified` email.** A provider that returns no verified email → login refused with a clear error.
4. **GitHub is OAuth2, not OIDC** — no `id_token`; fetch `/user` + `/user/emails` for the verified-primary email and use the numeric `id` as the stable subject. Google/Microsoft/generic use OIDC discovery (`server_metadata_url`) and the `sub` claim.

---

## File Structure

**Create:**
- `core/auth/providers.py` — `ProviderType` literal + `PRESETS` (github/google/microsoft endpoint+scope templates) + `normalize_provider_config()`
- `core/auth/oauth_registry.py` — `OAuthRegistry` (wraps authlib `OAuth`; `rebuild(providers)`, `client_for(id)`, `enabled_public_list()`)
- `storage/auth_provider_repo.py` — `AuthProviderRow`, `PostgresAuthProviderRepository`
- `storage/lite/auth_provider_repo.py` — `SqliteAuthProviderRepository`
- `storage/federated_identity_repo.py` — `FederatedIdentityRow`, `PostgresFederatedIdentityRepository`
- `storage/lite/federated_identity_repo.py` — `SqliteFederatedIdentityRepository`
- `apps/api/routers/oauth.py` — `/auth/oauth/{provider}/start` + `/callback`, `GET /auth/providers`
- `apps/api/routers/auth_providers_admin.py` — `/config/auth/providers` CRUD (or add into `routers/config.py`; see Task 7)
- `schemas/auth_providers.py` — provider request/response models
- `tests/support/oauth_fakes.py` — deterministic fake token/userinfo + a helper to register a fake provider
- `ui/app/(main)/settings/identity/` or a panel in Settings — provider admin
- Test files per task

**Modify:**
- `pyproject.toml` / `requirements.lock.txt` — add `authlib>=1.3.1`
- `core/config.py` — `oauth_state_secret: str` setting
- `storage/repositories.py` — Protocols for the 2 new repos
- `apps/api/state.py` — `auth_provider_repo`, `federated_identity_repo` fields
- `apps/api/lifespan.py` — construct + ensure_schema (server + lite); build `OAuthRegistry` on `app.state`; add SessionMiddleware
- `apps/api/main.py` — add SessionMiddleware; include oauth router
- `apps/api/dependencies.py` — getters + Dep aliases for the 2 repos + the registry
- `apps/api/routers/auth.py` — extract the user+membership+first-owner creation into a reusable `provision_user(...)` helper the callback can call
- `ui/lib/auth.ts`, `ui/app/(auth)/login/page.tsx`, `ui/lib/types.ts`

---

## Task 1: authlib dep + provider presets

**Files:** Modify `pyproject.toml`, `requirements.lock.txt`; Create `core/auth/providers.py`; Test `tests/test_auth_providers_presets.py`

- [ ] **Step 1: Add dep.** Add `"authlib>=1.3.1"` to `pyproject.toml` dependencies; `pip install authlib`; pin it (+ any transitive it resolves, matching lock-file style) in `requirements.lock.txt`. Confirm `python -c "import authlib; print(authlib.__version__)"`.

- [ ] **Step 2: Failing test** `tests/test_auth_providers_presets.py`:

```python
from core.auth.providers import PRESETS, PROVIDER_TYPES, build_register_kwargs

def test_known_presets_exist():
    assert {"github", "google", "microsoft", "oidc"} <= set(PROVIDER_TYPES)

def test_google_uses_discovery():
    kw = build_register_kwargs(provider_type="google", client_id="cid", client_secret="sec", discovery_url=None, scopes=None)
    assert kw["server_metadata_url"].startswith("https://accounts.google.com")
    assert "openid" in kw["client_kwargs"]["scope"]
    assert kw["client_kwargs"]["code_challenge_method"] == "S256"

def test_github_is_oauth_not_oidc():
    kw = build_register_kwargs(provider_type="github", client_id="cid", client_secret="sec", discovery_url=None, scopes=None)
    assert "server_metadata_url" not in kw
    assert kw["access_token_url"].startswith("https://github.com")
    assert "user:email" in kw["client_kwargs"]["scope"]

def test_generic_oidc_requires_discovery_url():
    kw = build_register_kwargs(provider_type="oidc", client_id="c", client_secret="s", discovery_url="https://idp.example/.well-known/openid-configuration", scopes="openid email")
    assert kw["server_metadata_url"] == "https://idp.example/.well-known/openid-configuration"
```

- [ ] **Step 3: Run → fail.** `python -m pytest tests/test_auth_providers_presets.py -q`

- [ ] **Step 4: Implement** `core/auth/providers.py`:
  - `PROVIDER_TYPES = ("github", "google", "microsoft", "oidc")`; `ProviderType = Literal[...]`.
  - `IS_OIDC = {"google": True, "microsoft": True, "oidc": True, "github": False}`.
  - `PRESETS`: per type, the static bits — google: `server_metadata_url="https://accounts.google.com/.well-known/openid-configuration"`, default scope `"openid email profile"`. microsoft: `server_metadata_url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"` (note: `common` tenant; a future per-provider tenant override can replace `common`), scope `"openid email profile"`. github: `authorize_url="https://github.com/login/oauth/authorize"`, `access_token_url="https://github.com/login/oauth/access_token"`, `api_base_url="https://api.github.com/"`, scope `"read:user user:email"`. oidc: uses caller-supplied `discovery_url` as `server_metadata_url`, scope default `"openid email profile"`.
  - `build_register_kwargs(*, provider_type, client_id, client_secret, discovery_url, scopes) -> dict`: returns the authlib `oauth.register(...)` kwargs. Always set `client_id`, `client_secret`, and `client_kwargs={"scope": <scopes or preset default>, "code_challenge_method": "S256"}` for OIDC providers (GitHub: include scope but PKCE optional — GitHub supports PKCE; include it). For `oidc`, require `discovery_url` (raise `ValueError` if falsy). For microsoft/google use the preset `server_metadata_url`.
  - `normalize_provider_type(value) -> str` raising `ValueError` on unknown.

- [ ] **Step 5: Run → pass.** Then `python -m pytest tests/ -q` (no regressions).

- [ ] **Step 6: Commit.** `git add -A && git commit -m "feat(auth): authlib dep + OIDC/OAuth provider presets"`

---

## Task 2: auth_providers repository (pg + lite)

**Files:** Modify `storage/repositories.py`; Create `storage/auth_provider_repo.py`, `storage/lite/auth_provider_repo.py`; Test `tests/test_auth_provider_repo.py`

Table `auth_providers`: `id TEXT PK, provider_type TEXT, display_name TEXT, client_id TEXT, client_secret TEXT, discovery_url TEXT, scopes TEXT, enabled BOOLEAN, created_at, updated_at`. Multi-row. Follow `storage/api_token_repo.py` + the Phase-1 `org_repo` for engine/text/tracer conventions.

- [ ] **Step 1: Failing test** (lite engine fixture, mirror `tests/test_org_repo.py`):

```python
import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.auth_provider_repo import SqliteAuthProviderRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "p.db"))
    r = SqliteAuthProviderRepository(engine)
    await r.ensure_schema()
    return r

async def test_create_get_list(repo):
    row = await repo.create(id="p1", provider_type="google", display_name="Google", client_id="cid", client_secret="sec", discovery_url=None, scopes="openid email", enabled=True)
    assert row.provider_type == "google" and row.enabled is True
    assert (await repo.get("p1")).client_id == "cid"
    assert len(await repo.list_all()) == 1

async def test_list_enabled_only(repo):
    await repo.create(id="p1", provider_type="google", display_name="G", client_id="c", client_secret="s", discovery_url=None, scopes=None, enabled=True)
    await repo.create(id="p2", provider_type="github", display_name="GH", client_id="c", client_secret="s", discovery_url=None, scopes=None, enabled=False)
    en = await repo.list_enabled()
    assert {p.id for p in en} == {"p1"}

async def test_update_and_set_enabled(repo):
    await repo.create(id="p1", provider_type="google", display_name="G", client_id="c", client_secret="s", discovery_url=None, scopes=None, enabled=False)
    await repo.update(id="p1", client_id="c2", client_secret="s2", scopes="openid", display_name="Google2", discovery_url=None)
    await repo.set_enabled(id="p1", enabled=True)
    row = await repo.get("p1")
    assert row.client_id == "c2" and row.enabled is True and row.display_name == "Google2"

async def test_delete(repo):
    await repo.create(id="p1", provider_type="google", display_name="G", client_id="c", client_secret="s", discovery_url=None, scopes=None, enabled=True)
    await repo.delete("p1")
    assert await repo.get("p1") is None
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Protocol** in `storage/repositories.py`:

```python
@runtime_checkable
class AuthProviderRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def create(self, *, id: str, provider_type: str, display_name: str, client_id: str, client_secret: str, discovery_url: str | None, scopes: str | None, enabled: bool) -> "AuthProviderRow": ...
    async def get(self, id: str) -> "AuthProviderRow | None": ...
    async def list_all(self) -> "list[AuthProviderRow]": ...
    async def list_enabled(self) -> "list[AuthProviderRow]": ...
    async def update(self, *, id: str, display_name: str, client_id: str, client_secret: str, discovery_url: str | None, scopes: str | None) -> "AuthProviderRow": ...
    async def set_enabled(self, *, id: str, enabled: bool) -> None: ...
    async def delete(self, id: str) -> None: ...
```
(`AuthProviderRow` under TYPE_CHECKING.)

- [ ] **Step 4: Postgres** `storage/auth_provider_repo.py`. `AuthProviderRow(id, provider_type, display_name, client_id, client_secret, discovery_url, scopes, enabled, created_at, updated_at)`. `update`/`set_enabled` bump `updated_at=now()`. Read-back via `get` (RuntimeError if None).

- [ ] **Step 5: Lite** `storage/lite/auth_provider_repo.py` — reuse `AuthProviderRow`; SQLite types; `enabled` as INTEGER 0|1 → bool at the boundary; `_parse_dt` for timestamps.

- [ ] **Step 6: Export** both from `storage/__init__.py` (sorted; add to `__all__`).

- [ ] **Step 7: Run → pass; full suite.**

- [ ] **Step 8: Commit.** `git add -A && git commit -m "feat(storage): auth_providers repo (pg + lite)"`

---

## Task 3: federated_identities repository (pg + lite)

**Files:** Modify `storage/repositories.py`; Create `storage/federated_identity_repo.py`, `storage/lite/federated_identity_repo.py`; Test `tests/test_federated_identity_repo.py`

Table `federated_identities`: `id TEXT PK, user_id TEXT, provider TEXT, subject TEXT, email TEXT, created_at`, with `UNIQUE(provider, subject)`. `provider` here = the auth_providers row id (so two Google configs stay distinct) — but ALSO store `provider_type` if useful; keep it simple: store the **provider id**. Account-linking is by verified email at the router level; this table records the binding.

- [ ] **Step 1: Failing test** (lite fixture):

```python
import pytest
from storage.lite.engine import make_sqlite_engine
from storage.lite.federated_identity_repo import SqliteFederatedIdentityRepository

@pytest.fixture
async def repo(tmp_path):
    engine = make_sqlite_engine(str(tmp_path / "f.db"))
    r = SqliteFederatedIdentityRepository(engine)
    await r.ensure_schema()
    return r

async def test_add_and_get_by_subject(repo):
    row = await repo.add(id="i1", user_id="u1", provider="p-google", subject="sub-123", email="a@b.c")
    assert row.user_id == "u1"
    got = await repo.get_by_subject(provider="p-google", subject="sub-123")
    assert got is not None and got.user_id == "u1"

async def test_get_by_subject_absent(repo):
    assert await repo.get_by_subject(provider="p-google", subject="nope") is None

async def test_list_for_user(repo):
    await repo.add(id="i1", user_id="u1", provider="p-google", subject="s1", email="a@b.c")
    await repo.add(id="i2", user_id="u1", provider="p-github", subject="s2", email="a@b.c")
    assert len(await repo.list_for_user("u1")) == 2

async def test_unique_provider_subject(repo):
    await repo.add(id="i1", user_id="u1", provider="p-google", subject="s1", email="a@b.c")
    with pytest.raises(Exception):
        await repo.add(id="i2", user_id="u2", provider="p-google", subject="s1", email="x@y.z")
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Protocol:**

```python
@runtime_checkable
class FederatedIdentityRepository(Protocol):
    async def ensure_schema(self) -> None: ...
    async def add(self, *, id: str, user_id: str, provider: str, subject: str, email: str) -> "FederatedIdentityRow": ...
    async def get_by_subject(self, *, provider: str, subject: str) -> "FederatedIdentityRow | None": ...
    async def list_for_user(self, user_id: str) -> "list[FederatedIdentityRow]": ...
```

- [ ] **Step 4: Postgres + Step 5: Lite** — mirror Task 2 conventions. `UNIQUE(provider, subject)` raises on dup (test expects it).

- [ ] **Step 6: Export; Step 7: run pass + full suite; Step 8: Commit** `git add -A && git commit -m "feat(storage): federated_identities repo (pg + lite)"`

---

## Task 4: OAuthRegistry

**Files:** Create `core/auth/oauth_registry.py`, `tests/test_oauth_registry.py`

Wraps authlib `authlib.integrations.starlette_client.OAuth`. Built from `AuthProviderRow`s. Rebuilds on provider change.

- [ ] **Step 1: Failing test:**

```python
from core.auth.oauth_registry import OAuthRegistry
from storage.auth_provider_repo import AuthProviderRow

def _row(id, t, enabled=True):
    return AuthProviderRow(id=id, provider_type=t, display_name=t.title(), client_id="cid", client_secret="sec",
                           discovery_url=("https://idp/.well-known/openid-configuration" if t == "oidc" else None),
                           scopes=None, enabled=enabled, created_at=None, updated_at=None)

def test_rebuild_registers_enabled_only():
    reg = OAuthRegistry()
    reg.rebuild([_row("p1", "google"), _row("p2", "github", enabled=False)])
    assert reg.client_for("p1") is not None
    assert reg.client_for("p2") is None  # disabled not registered
    assert reg.client_for("nope") is None

def test_public_list_masks_secrets():
    reg = OAuthRegistry()
    reg.rebuild([_row("p1", "google")])
    pub = reg.enabled_public_list()
    assert pub == [{"id": "p1", "provider_type": "google", "display_name": "Google"}]
    # no client_id / client_secret in the public list
    assert all("client_secret" not in p and "client_id" not in p for p in pub)
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** `OAuthRegistry`:
  - `__init__`: `self._oauth = OAuth()` is rebuilt each time (authlib `OAuth` registrations are additive and can't be cleanly cleared; on `rebuild` construct a **fresh** `OAuth()` instance and re-register, then swap). Keep `self._enabled: list[dict]` for the public list and `self._ids: set[str]`.
  - `rebuild(self, providers: list[AuthProviderRow]) -> None`: new `OAuth()`; for each enabled provider, `oauth.register(name=row.id, **build_register_kwargs(provider_type=row.provider_type, client_id=row.client_id, client_secret=row.client_secret, discovery_url=row.discovery_url, scopes=row.scopes))`. Store public dicts. Swap `self._oauth`.
  - `client_for(self, id: str)`: return `getattr(self._oauth, id, None)` only if id was registered+enabled (guard via `self._ids`), else None.
  - `enabled_public_list(self) -> list[dict]`: `[{"id","provider_type","display_name"}]`.
  - `provider_type_for(self, id) -> str | None` (callback needs to know github-vs-oidc).
  - Lazy-import authlib inside the module top (it's now a dep).

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit.** `git add -A && git commit -m "feat(auth): OAuthRegistry over authlib (enabled-provider registry)"`

---

## Task 5: Wire repos + registry + SessionMiddleware into lifespan/state/deps

**Files:** Modify `apps/api/state.py`, `apps/api/lifespan.py`, `apps/api/main.py`, `apps/api/dependencies.py`, `core/config.py`; Test `tests/test_oauth_wiring.py`

- [ ] **Step 1: Setting.** `core/config.py`: add `oauth_state_secret: str = Field(default="")` (when empty, derive a stable per-process fallback from `mcp_api_key` or a constant + log a warning that federation cookies won't survive restart/secret-rotation across instances — acceptable for single-node).

- [ ] **Step 2: Failing test** `tests/test_oauth_wiring.py` (sync TestClient lite, like `tests/test_auth_lifespan_wiring.py` — avoids the async-teardown cancel-scope issue):

```python
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def lite_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MODE", "lite")
    monkeypatch.setenv("LITE_DATA_DIR", str(tmp_path / ".memcl"))
    from core.config import get_settings
    get_settings.cache_clear()
    from apps.api.main import app
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()

def test_provider_repos_and_registry_present(lite_client):
    app = lite_client.app
    assert app.state.oauth_registry is not None
    state = app.state.app_state
    assert state.auth_provider_repo is not None
    assert state.federated_identity_repo is not None

def test_providers_endpoint_empty_by_default(lite_client):
    r = lite_client.get("/auth/providers")
    assert r.status_code == 200 and r.json() == {"providers": []}
```

- [ ] **Step 3: AppState** (`state.py`): add `auth_provider_repo: AuthProviderRepository | None = None`, `federated_identity_repo: FederatedIdentityRepository | None = None`. Thread through `with_default_embedder`.

- [ ] **Step 4: lifespan server + lite.** Construct the two Postgres repos in `_build_state` and the SQLite ones in `_build_lite_state`. In `lifespan()` identity block: `ensure_schema()` both; build `OAuthRegistry()`, `reg.rebuild(await auth_provider_repo.list_enabled())`, `app.state.oauth_registry = reg`.

- [ ] **Step 5: SessionMiddleware** in `apps/api/main.py` `create_app()`: `from starlette.middleware.sessions import SessionMiddleware; app.add_middleware(SessionMiddleware, secret_key=<resolved oauth_state_secret>, session_cookie="memcl_oauth", max_age=300, same_site="lax", https_only=<prod>)`. Resolve the secret from settings at app-create time (it's process-level, not runtime-config — fine). Place it so it wraps the oauth routes.

- [ ] **Step 6: dependencies.py:** `get_auth_provider_repo`/`AuthProviderRepoDep`, `get_federated_identity_repo`/`FederatedIdentityRepoDep`, `get_oauth_registry`/`OAuthRegistryDep` (reads `request.app.state.oauth_registry`). Mirror `get_token_cache`.

- [ ] **Step 7: Run** the wiring test (the `/auth/providers` endpoint arrives in Task 7 — for now, split: implement a minimal `GET /auth/providers` in this task returning `{"providers": reg.enabled_public_list()}` so the wiring test passes, OR move `test_providers_endpoint_empty_by_default` to Task 7). Simplest: add the tiny public `GET /auth/providers` route here (it only needs the registry). Then run `python -m pytest tests/test_oauth_wiring.py -q` and the FULL suite (TestClient boot must stay green; watch for SessionMiddleware breaking existing tests — it shouldn't, it's inert unless `request.session` is touched).

- [ ] **Step 8: Commit.** `git add -A && git commit -m "feat(api): wire auth-provider repos + OAuthRegistry + SessionMiddleware + GET /auth/providers"`

---

## Task 6: Provider config endpoints (`/config/auth/providers`)

**Files:** Create `schemas/auth_providers.py`; Modify `apps/api/routers/config.py` (or a new `routers/auth_providers_admin.py` included in main); Test `tests/test_auth_providers_admin.py`

Admin surface to add/list/update/enable/delete providers. Gated by `_require_bootstrap_or_authed` (Phase-1 pattern: bootstrap-open until configured, then require key or authenticated session). Secrets masked on read. **Every mutation calls `oauth_registry.rebuild(await repo.list_enabled())`** so changes take effect with no restart.

- [ ] **Step 1: Failing test** (lite async client from `tests/test_auth_router.py`):

```python
# happy path: register a user (bootstrap owner) so we're authed, then CRUD a provider
async def test_provider_crud_and_masking(client):
    await client.post("/auth/register", json={"email":"o@x.c","password":"password123","display_name":"O"})
    create = await client.post("/config/auth/providers", json={
        "provider_type":"google","display_name":"Google","client_id":"cid","client_secret":"shhh","scopes":"openid email"})
    assert create.status_code == 200
    pid = create.json()["id"]
    lst = await client.get("/config/auth/providers")
    body = lst.json()["providers"][0]
    assert body["client_id"] == "cid"
    assert "client_secret" not in body            # raw secret never returned
    assert body["has_secret"] is True
    assert body["enabled"] is False               # created disabled until toggled on
    # enable it
    en = await client.post(f"/config/auth/providers/{pid}/enable", json={"enabled": True})
    assert en.status_code == 200
    assert (await client.get("/auth/providers")).json()["providers"][0]["id"] == pid  # now public-listed
    # delete
    assert (await client.delete(f"/config/auth/providers/{pid}")).status_code == 200
    assert (await client.get("/auth/providers")).json() == {"providers": []}

async def test_provider_create_requires_auth_when_configured(client):
    # configure a key, then anonymous create must 401
    await client.post("/config/mcp-key/generate")
    await client.post("/auth/register", json={"email":"o@x.c","password":"password123","display_name":"O"})
    await client.post("/auth/logout")
    r = await client.post("/config/auth/providers", json={"provider_type":"google","display_name":"G","client_id":"c","client_secret":"s"})
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Schemas** `schemas/auth_providers.py`: `ProviderCreate(provider_type, display_name, client_id, client_secret, discovery_url: str|None=None, scopes: str|None=None)` with `model_config={"extra":"forbid"}` + a validator that `provider_type=="oidc"` requires `discovery_url`. `ProviderUpdate` (same minus type). `ProviderView(id, provider_type, display_name, client_id, has_secret: bool, discovery_url, scopes, enabled)`. `ProviderListResponse(providers: list[ProviderView])`. `EnableRequest(enabled: bool)`.

- [ ] **Step 4: Endpoints** (new providers created **disabled** by default — operator enables after verifying):
  - `POST /config/auth/providers` → validate type via `normalize_provider_type`; `id = secrets.token_urlsafe(8)`; `repo.create(..., enabled=False)`; rebuild registry (no-op since disabled); return `{"id": id}` + ProviderView.
  - `GET /config/auth/providers` → `repo.list_all()` → ProviderView (mask secret → `has_secret`).
  - `PATCH /config/auth/providers/{id}` → `repo.update(...)`; rebuild.
  - `POST /config/auth/providers/{id}/enable` (body EnableRequest) → `repo.set_enabled(...)`; **rebuild registry**.
  - `DELETE /config/auth/providers/{id}` → `repo.delete`; rebuild.
  - All gated with `_require_bootstrap_or_authed(runtime, api_key, principal)`; inject `OAuthRegistryDep` + `AuthProviderRepoDep`. On enable, if building the authlib client raises (e.g. bad discovery), surface a 400 with the message (don't crash the registry — rebuild from the persisted state and let the bad one be skipped/reported). Keep it simple: rebuild wraps each register in try/except and the endpoint re-reads to confirm the provider ended up registered; if not, return 400 "provider config invalid".

- [ ] **Step 5: Run → pass; full suite.**

- [ ] **Step 6: Commit.** `git add -A && git commit -m "feat(api): /config/auth/providers admin CRUD (masked secrets, live registry rebuild)"`

---

## Task 7: Refactor — extract `provision_user` from the auth router

**Files:** Modify `apps/api/routers/auth.py`; Test: existing `tests/test_auth_router.py` stays green

The OIDC callback needs the SAME "create user + membership + first-user→owner" logic register uses. Extract it so both call one helper (DRY; avoids drift).

- [ ] **Step 1:** Extract `async def provision_user(*, email, display_name, user_repo, membership_repo, password_hash: str | None = None) -> tuple[str, str]` returning `(user_id, role)`:
  - `is_bootstrap = await user_repo.count_users() == 0`
  - create user (token_urlsafe(12)); if `password_hash` is not None → `set_password`
  - role = ROLE_OWNER if is_bootstrap else ROLE_MEMBER; add_member in DEFAULT_ORG_ID
  - return `(user_id, role)`
- [ ] **Step 2:** Rewrite `/auth/register` to call `provision_user(..., password_hash=hash_password(body.password))`. Behavior must be **identical** — run `tests/test_auth_router.py` + `tests/test_get_principal.py` + `tests/test_auth_enforcement.py` (all must still pass, no test changes).
- [ ] **Step 3: Full suite.**
- [ ] **Step 4: Commit.** `git add -A && git commit -m "refactor(auth): extract provision_user helper (shared by register + oauth)"`

---

## Task 8: OAuth flow router + account-linking (the security-critical task)

**Files:** Create `apps/api/routers/oauth.py`, `tests/support/oauth_fakes.py`, `tests/test_oauth_flow.py`; Modify `apps/api/main.py` (include router)

Flow: `GET /auth/oauth/{provider_id}/start` → `authlib.authorize_redirect` (302 to provider, sets handshake cookie). `GET /auth/oauth/{provider_id}/callback` → `authorize_access_token` → resolve **verified email + stable subject** → link/create → `_create_session` → 302 to the UI (`/`).

**Linking algorithm (in the callback):**
1. `client = registry.client_for(provider_id)`; if None → 404/400 "provider not enabled".
2. `token = await client.authorize_access_token(request)` (authlib verifies state+nonce+PKCE+exchanges code).
3. Resolve `(subject, verified_email, display_name)`:
   - OIDC (`provider_type != github`): `info = token.get("userinfo")` (authlib parses id_token); `subject = info["sub"]`; `verified_email = info["email"] if info.get("email_verified") else None`; `display_name = info.get("name") or verified_email`. (Microsoft: treat `email` as verified even without the flag — see note; prefer `email_verified` when present, else fall back to `email` for `provider_type=="microsoft"`.)
   - GitHub: `u = (await client.get("user")).json()`; `subject = str(u["id"])`; `emails = (await client.get("user/emails")).json()`; `verified_email = next((e["email"] for e in emails if e.get("verified") and e.get("primary")), None) or next((e["email"] for e in emails if e.get("verified")), None)`; `display_name = u.get("name") or u.get("login") or verified_email`.
4. **Existing federated identity?** `fid = await federated_identity_repo.get_by_subject(provider=provider_id, subject=subject)`. If found → `user_id = fid.user_id` (straight login).
5. Else require `verified_email` (else 400 "provider did not supply a verified email — cannot create account"). Normalize lower. **Link by verified email:** `existing = await user_repo.get_by_email(verified_email)`. If found → `user_id = existing.user_id`. Else → `(user_id, _role) = await provision_user(email=verified_email, display_name=display_name, user_repo, membership_repo, password_hash=None)`. Either way → `federated_identity_repo.add(id=token_urlsafe(12), user_id=user_id, provider=provider_id, subject=subject, email=verified_email)`.
6. `await _create_session(user_id=user_id, org_id=DEFAULT_ORG_ID, response=..., session_repo, session_cache)`. Return `RedirectResponse("/")` carrying the Set-Cookie.

**Security notes to honor in code:** account-link ONLY on a `verified` email; `state`/`nonce`/PKCE are authlib's job (don't reimplement); never log token/secret; the `start` endpoint must reject an unknown/disabled provider before redirecting; the callback must reject if `authorize_access_token` raises (let it 400, do not create anything).

- [ ] **Step 1: Fakes** `tests/support/oauth_fakes.py`: a `FakeOAuthClient` with `async authorize_access_token(request)` returning a canned `{"userinfo": {...}}` (configurable sub/email/email_verified/name) and `async get(path)` returning canned GitHub `/user` + `/user/emails` JSON; and `install_fake_provider(app, *, provider_id, provider_type, userinfo=..., github_user=..., github_emails=...)` that creates+enables an `auth_providers` row via the repo AND monkeypatches `app.state.oauth_registry.client_for` (or the registry's internal map) to return the fake for that id. Also a fake for `authorize_redirect` returning a `RedirectResponse` to a dummy URL so `/start` is testable without real provider metadata.

- [ ] **Step 2: Failing tests** `tests/test_oauth_flow.py` (lite async client; build the app, then install fakes against `client._transport.app` / the running app instance the fixture exposes — adapt the fixture to also yield `app`):

```python
async def test_start_redirects_for_enabled_provider(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider
    await install_fake_provider(app, provider_id="p-google", provider_type="google",
                                userinfo={"sub":"g-1","email":"new@x.c","email_verified":True,"name":"New"})
    r = await c.get("/auth/oauth/p-google/start", follow_redirects=False)
    assert r.status_code in (302, 307)

async def test_callback_creates_first_user_as_owner(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider
    await install_fake_provider(app, provider_id="p-google", provider_type="google",
                                userinfo={"sub":"g-1","email":"new@x.c","email_verified":True,"name":"New"})
    r = await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code in (302, 307)             # redirected to UI
    me = await c.get("/auth/me")
    body = me.json()
    assert body["authenticated"] is True and body["user"]["email"] == "new@x.c"
    assert body["user"]["roles"] == ["owner"]      # first-ever user

async def test_callback_links_to_existing_local_user_by_verified_email(client_and_app):
    c, app = client_and_app
    # local user exists first (becomes owner), then logs out
    await c.post("/auth/register", json={"email":"a@b.c","password":"password123","display_name":"A"})
    await c.post("/auth/logout")
    from tests.support.oauth_fakes import install_fake_provider
    await install_fake_provider(app, provider_id="p-google", provider_type="google",
                                userinfo={"sub":"g-9","email":"a@b.c","email_verified":True,"name":"A"})
    await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    me = (await c.get("/auth/me")).json()
    assert me["authenticated"] and me["user"]["email"] == "a@b.c"
    assert me["user"]["roles"] == ["owner"]        # linked to the EXISTING owner, no new user

async def test_callback_refuses_unverified_email(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider
    await install_fake_provider(app, provider_id="p-google", provider_type="google",
                                userinfo={"sub":"g-2","email":"u@x.c","email_verified":False,"name":"U"})
    r = await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    assert r.status_code == 400
    assert (await c.get("/auth/me")).json()["authenticated"] is False

async def test_second_oauth_login_same_subject_is_idempotent(client_and_app):
    c, app = client_and_app
    from tests.support.oauth_fakes import install_fake_provider
    await install_fake_provider(app, provider_id="p-google", provider_type="google",
                                userinfo={"sub":"g-1","email":"new@x.c","email_verified":True,"name":"New"})
    await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    await c.post("/auth/logout")
    await c.get("/auth/oauth/p-google/callback?code=x&state=y", follow_redirects=False)
    me = (await c.get("/auth/me")).json()
    assert me["authenticated"] is True
    # still exactly one user
    # (assert via an admin/list path or by re-registering a new email and checking owner already taken)
```

(The implementer must adapt the `client_and_app` fixture — extend the Phase-1 fixture to `yield c, app`. The fake must bypass authlib's real state/PKCE verification since there's no real handshake; `install_fake_provider` overriding `client_for(provider_id)` to return a `FakeOAuthClient` is what makes `authorize_access_token` return canned data without a real provider.)

- [ ] **Step 3: Run → fail.**

- [ ] **Step 4: Implement** `apps/api/routers/oauth.py` per the algorithm above. `router = APIRouter(prefix="/auth/oauth", tags=["oauth"])`. `/start`: `client = registry.client_for(pid)`; 404 if None; `redirect_uri = str(request.url_for("oauth_callback", provider_id=pid))`; `return await client.authorize_redirect(request, redirect_uri)`. `/callback` (name="oauth_callback"): the linking algorithm; wrap `authorize_access_token` in try/except → 400 on failure. Inject `AuthProviderRepoDep`, `FederatedIdentityRepoDep`, `UserRepoDep`, `MembershipRepoDep`, `SessionRepoDep`, `SessionCacheDep`, `OAuthRegistryDep`. Determine `provider_type` via `registry.provider_type_for(pid)` (or read the row). Include the router in `main.py`.

- [ ] **Step 5: Run → pass** (`tests/test_oauth_flow.py`), then FULL suite green.

- [ ] **Step 6: Commit.** `git add -A && git commit -m "feat(api): OIDC/OAuth login flow + verified-email account linking"`

---

## Task 9: UI — provider buttons + Settings → Identity admin

**Files:** Modify `ui/lib/auth.ts`, `ui/lib/types.ts`, `ui/app/(auth)/login/page.tsx`; Create a Settings → Identity panel (`ui/app/(main)/settings/...` — match the existing Settings panel structure from Phase 1 / `TokensPanel`). Build gate: `cd ui && npm run build`.

- [ ] **Step 1:** `ui/lib/types.ts`: `ProviderPublic {id, provider_type, display_name}`, `ProviderAdmin {id, provider_type, display_name, client_id, has_secret, discovery_url, scopes, enabled}`.
- [ ] **Step 2:** `ui/lib/auth.ts`: `fetchProviders()` → `GET /auth/providers` (public list for login buttons). Admin helpers: `listProviderConfigs()`, `createProvider(...)`, `updateProvider(...)`, `setProviderEnabled(id, enabled)`, `deleteProvider(id)` hitting `/config/auth/providers*`.
- [ ] **Step 3:** Login page: call `fetchProviders()`; for each, render a "Continue with {display_name}" button that does `window.location.href = "/api/auth/oauth/{id}/start"` (full-page nav so the provider redirect + cookie flow works — NOT fetch). Keep the local email/password form below a divider.
- [ ] **Step 4:** Settings → Identity panel (emerald style): list configured providers (type, display name, masked secret, enabled toggle), an "Add provider" form (type dropdown github/google/microsoft/generic-oidc; client-id; client-secret; discovery URL shown only for generic; scopes optional), enable/disable toggle, delete. Make clear secrets are write-only. Note in the UI copy that the **redirect/callback URL** to register at the provider is `{origin}/api/auth/oauth/{id}/callback` (show it after creation so the operator can paste it into GitHub/Google console).
- [ ] **Step 5:** `cd ui && npm run build` must pass. Then return to repo root.
- [ ] **Step 6: Commit.** `git add -A && git commit -m "feat(ui): federated login buttons + Settings Identity provider admin"`

---

## Task 10: Docs + final gates

**Files:** Modify `docs/22_SECURITY_AND_ACCESS_CONTROL.md`, `docs/06_CONFIGURATION.md`, `docs/26_GLOSSARY.md`, `docs/07_API_REFERENCE.md`

- [ ] **Step 1:** `22_SECURITY`: add a "Federated login (Phase 2)" subsection — supported providers, the generic-OIDC discovery option, account-linking-by-verified-email rule, PKCE+state+nonce, the redirect-URL operators must register, secrets stored server-side & masked, providers created disabled until enabled. Note teams/RBAC still Phase 3.
- [ ] **Step 2:** `06_CONFIGURATION`: add `OAUTH_STATE_SECRET` (handshake-cookie signing; set in prod for multi-instance/restart stability). Note providers are runtime-configured (no env needed).
- [ ] **Step 3:** `07_API_REFERENCE`: add `/auth/providers`, `/auth/oauth/{id}/start`, `/auth/oauth/{id}/callback`, and the `/config/auth/providers*` suite.
- [ ] **Step 4:** `26_GLOSSARY`: Federated identity, OIDC provider, Account linking, PKCE.
- [ ] **Step 5: Gates.** `python -m pytest tests/ -q` (all green). `cd ui && npm run build` (clean). Lite route check: `python -c "import os; os.environ['MODE']='lite'; from apps.api.main import app; print(sorted({r.path for r in app.routes if '/auth/oauth' in getattr(r,'path','') or getattr(r,'path','')=='/auth/providers'}))"` → shows the oauth routes.
- [ ] **Step 6: Commit.** `git add -A && git commit -m "docs: document Phase-2 federation (providers, OIDC/OAuth login, account linking)"`

---

## Done criteria (Phase 2)

- Operator can add a provider (GitHub/Google/Microsoft/generic-OIDC) in Settings, paste client-id/secret, see the callback URL, and enable it — no restart; the OAuthRegistry rebuilds live.
- `GET /auth/providers` lists enabled providers; login page renders a button per provider.
- A federated login: new verified-email user is created (first-ever → owner, else member) and bound in `federated_identities`; a returning subject logs straight in; a verified email matching an existing (local or federated) user **links** to that user; an unverified-email provider response is refused.
- Local password login + agents/API tokens/MCP unchanged (no regression).
- Server + lite both boot with the two new tables; full pytest green; `ui` build green.
- All flow tests pass against the deterministic fake (no real provider, no network). Real-provider verification is a documented manual step for when the operator supplies credentials.

**Next:** Phase 3 (teams, invitations, per-repo grants — fine-grained RBAC), planned after Phase 2 merges. Real-provider smoke test with the user's GitHub/Google/Microsoft client-IDs happens when they're back at the laptop.
