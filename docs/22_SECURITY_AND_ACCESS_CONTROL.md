# 22 · Security + Access Control

← back to [index](00_INDEX.md) · related: [16_AUDIT_AND_GOVERNANCE](16_AUDIT_AND_GOVERNANCE.md), [21_DEPLOYMENT](21_DEPLOYMENT.md), [08_MCP_TOOLING](08_MCP_TOOLING.md)

Five layers of access control — identity milestone (Phases 1–3) complete:

1. **Human identity** — Organizations, Users, Memberships, and server-side Sessions (Phase 1).
2. **Federated login** — OIDC/OAuth providers (GitHub, Google, Microsoft, generic OIDC) alongside local credentials (Phase 2).
3. **Teams + per-repo RBAC** — Teams, invitations, and fine-grained per-repo grants enforced on the human path (Phase 3).
4. **Auth at the network edge** — `MCP_API_KEY` / named tokens for the agent surface.
5. **Tenant isolation + policy engine** — `TenantManager` cross-org isolation; deterministic deny/allow policy rules.

## Human identity (Phase 1)

The human-identity layer adds durable Organizations (the tenant boundary for human users),
Users with local password credentials, Memberships linking users to orgs with a role, and
server-side Sessions.

### Core concepts

- **Organization** — the top-level tenant boundary. Every user belongs to at least one org.
- **User** — a human account with an email + argon2id-hashed password credential.
- **Membership** — a user↔org relationship carrying a role:
  `owner` | `admin` | `member` | `viewer`.
- **Session** — a server-side record tied to a browser via an httpOnly cookie. Only the
  SHA-256 hash of the session token is stored; the raw token is never persisted. Sessions are
  revocable and expire after `SESSION_TTL_SECONDS` (default 86 400 s / 24 h).

### Auth endpoints

| Endpoint | Who can call | Notes |
|---|---|---|
| `POST /auth/register` | First call: anyone (creates org + owner, auto-logs-in). Subsequent calls: owner or admin. | |
| `POST /auth/login` | Anyone with valid credentials | Sets httpOnly session cookie |
| `POST /auth/logout` | Authenticated session | Revokes session immediately |
| `GET /auth/me` | Authenticated session | Returns user + org + role |

### Principal resolution order

Every request resolves a `Principal` in this order:

1. **Session cookie** — httpOnly `memcl_session` cookie → server-side session lookup → human principal with role.
2. **API token / MCP key** — `X-API-Key` or `Authorization: Bearer` → agent principal.
3. **Anonymous** — no credentials; allowed only on unauthenticated endpoints.

Config-mutation endpoints (`/config/*`) accept an authenticated session **OR** the API key
**OR** bootstrap-open (no key configured). Agents continue to use API tokens unchanged.

### What is NOT in Phase 1

- **Federation (OIDC/OAuth — GitHub, Google, Microsoft)** — Phase 2.
- **Team invitations + per-repo grants + fine-grained RBAC** — Phase 3. Roles exist in the
  schema but per-repo authorization is not yet enforced beyond the existing tenant gate.

## Federated login (Phase 2)

Operators can add one or more **OAuth / OIDC identity providers** (GitHub, Google,
Microsoft, or any generic OIDC-compliant issuer) alongside local password login,
without a restart.

### Supported providers + runtime configuration

Providers are configured at runtime in **Settings → Identity** (or via the
`/config/auth/providers` API): supply a provider type, client-id, client-secret,
optional discovery URL (for generic OIDC), and scopes. The OAuthRegistry
rebuilds live — no restart required.

Newly created providers start **disabled**. Enable a provider only after you have:
1. Pasted the client-id and client-secret into the settings panel.
2. Registered the callback URL at the identity provider:
   `{origin}/api/auth/oauth/{provider_id}/callback`

### Login flow + security

`GET /auth/oauth/{id}/start` redirects the browser to the upstream provider.
The provider redirects back to `GET /auth/oauth/{id}/callback`, where
authlib validates the **state**, **nonce**, and **PKCE S256 code-verifier**.
The handshake state is kept in a dedicated httpOnly cookie (`memcl_oauth`,
managed by Starlette's `SessionMiddleware`) — separate from the identity
cookie `memcl_session`.

Client secrets are stored server-side and are **never returned by the API**
(the provider list exposes only `has_secret: bool`).

### Account linking by verified email

On a successful callback:

1. **Returning user** — a `(provider, subject)` pair already in
   `federated_identities` → logged in immediately.
2. **Existing user** — the provider's **verified** email matches an existing
   user (local or federated) → the new federated identity is linked to that
   user account and the session starts.
3. **New user** — no match → a fresh user is created (first-ever user on the
   instance becomes owner; subsequent users become members).

Providers that do **not** return a verified email are **refused (400)**.
GitHub is handled specially: Memory-CL fetches the verified primary email
from the GitHub `/user/emails` endpoint and uses the numeric GitHub user ID
as the OAuth subject.

### New tables

- **`auth_providers`** — per-provider config (type, client-id, scopes,
  discovery URL, enabled flag). `client_secret` is stored server-side; only
  `has_secret` is exposed via the API.
- **`federated_identities`** — user ↔ provider binding with a UNIQUE
  `(provider_id, subject)` constraint preventing duplicate linkage.

### What is NOT in Phase 2

**Team invitations, per-repo grants, and fine-grained RBAC** — Phase 3 (now complete; see below).

---

## Teams + per-repo RBAC (Phase 3)

Phase 3 completes the identity milestone. Everything in Phases 1–2 stays unchanged.

### Repos belong to orgs

`repo_registry.org_id` stamps every repo with its owning org (default `"default"`). Ingest
automatically tags the repo with the caller's org. Cross-org repo access is blocked at the
resolver layer, not just at the policy engine.

### Access model

| Principal | Effective repo access |
|---|---|
| **owner / admin** (org role) | Admin-level access to **all repos** in their org — no per-repo grant required. |
| **member** | Read + write on repos granted via a team or a direct user grant. |
| **viewer** | Read-only on granted repos; any write-level grant is silently capped at read. |
| Ungranted member / viewer | **403** on any repo-scoped endpoint (listed repos filtered out of `GET /repos`). |
| **Agent (API token / MCP key)** | Org-scoped full access to **all repos** in their org — by design (service-token model), not per-repo. The MCP tool surface is unchanged. |

### Teams

An org is subdivided into **teams** (sub-groups). A team can be granted access to one or more
repos; all members of the team inherit the grant. A user can belong to multiple teams within
the same org.

### Invitations (self-serve onboarding)

An org admin mints an invitation link. The recipient visits `/accept-invite`:
- **New user** — provides credentials → created at the invited role → logged in.
- **Existing logged-in user** → membership added/updated to the invited role.

Invitation state is tracked in the `org_invitations` table (token hash only; raw token shown once).

### Enforced endpoints

All human-path repo endpoints (`GET /repos`, `GET /repos/{id}/*`, `POST /ingest`, etc.) pass
through `RepoAccessResolver` before hitting storage. The resolver evaluates:
1. Org membership role (owner/admin → allow all).
2. Direct user grants (`repo_grants` where `grantee_type="user"`).
3. Team membership + team grants (`repo_grants` where `grantee_type="team"`).

`GET /repos` returns only repos the caller can access; ungranted repos are silently filtered.

### Non-breaking when unconfigured

When `JWT_SECRET` / auth is unconfigured (dev/bootstrap), no session is resolved and the
resolver falls through to the existing open mode — every repo endpoint behaves exactly as
before Phase 3. The current single-org homelab deployment is a no-op: agents carry the default
org and all repos belong to the default org, so full access is preserved.

---

## MCP API key

`apps/mcp/auth.py::require_mcp_api_key` is the FastAPI dependency
that gates the mutation surface: `POST /mcp/tools/{name}`,
`POST /ingest`, and `POST /ingest/reembed` (reembed spends
embedding-provider money, so it is never left open).

Behavior (`apps/mcp/token_auth.credential_accepted`, shared by the REST
dependency and the native-transport middleware so they can't diverge):

- Nothing configured (no key, no tokens) → dev mode; every request allowed.
- Otherwise a request must present `X-API-Key: <key>` OR
  `Authorization: Bearer <key>`, matching **either** the MCP key **or** an
  active named token. Wrong / missing → HTTP 401.

The `/mcp/tools` listing endpoint is intentionally unauthenticated —
discovering the surface is cheap and cannot leak data.

### Named, revocable API tokens

Beyond the single static key, operators can mint **multiple named tokens**
(one per agent/machine) and revoke any one individually — no shared-secret
rotation. Only a **SHA-256 hash** is stored (`api_tokens` table); the raw
token is shown once at creation and is unrecoverable.

- `POST /config/tokens {name}` — mint (returned once) · `GET /config/tokens`
  — masked list · `DELETE /config/tokens/{id}` — revoke (instant).
- CLI: `memcl token create <name> | list | revoke <id>`. UI: Settings → API
  tokens. Auth checks a cached active-hash set, so it stays O(1).

### Runtime config + no-restart key management

A runtime-config layer (Postgres `app_config`, **Postgres-over-env**) lets
operators change auth/embedding settings WITHOUT a restart, via the first-run
wizard (`/setup`) and `/config` endpoints: generate/rotate the MCP key
(`/config/mcp-key/generate|rotate`), set/clear the OpenAI key
(`/config/openai-key`), choose embedding mode (`/config/embedding-mode`), and
generate the git-webhook signing secret (`/config/webhook-secret/generate`).
The git push webhook (`POST /webhooks/git`) is verified by HMAC (GitHub) /
token (GitLab) — it never runs without a secret. When `app_config` is empty,
everything falls back to env (backward compatible — the live key keeps working).

```bash
# Production: a key carries from env on first boot, then becomes runtime-managed.
MCP_API_KEY=<seed-or-rotate-in-the-UI>
```

```bash
# CLI
export MEMCL_API_KEY=<same-key>
memcl status
```

```typescript
// SDK (TS)
new AsyncMemoryClient({ apiKey: process.env.MEMORY_CL_API_KEY })
```

```python
# SDK (Python)
AsyncMemoryClient(api_key=os.environ["MEMORY_CL_API_KEY"])
```

If you need org-wide auth on every endpoint (not just the MCP and
ingest mutation endpoints above), terminate
TLS + auth at a reverse proxy (nginx, Envoy, Cloudflare) in front of
the FastAPI process.

## Tenant isolation

`core/governance/tenant_manager.py::TenantManager`:

- Tenants are first-class objects (`Tenant.tenant_id` + name + budgets).
- Each repo is owned by exactly one tenant.
- Cross-tenant repo steal raises `CrossTenantAccessError`.

`AccessControl.check(AccessRequest)` calls `assert_owns_repo` first.
A failure short-circuits with `matched_policy="tenant_ownership"` and
emits a `policy_decide` audit event with `allowed=false`.

This is the spec's "no cross-tenant retrieval allowed" — pinned by
`test_access_control_denies_cross_tenant_access`.

## Policy engine

`core/governance/policy_engine.py::PolicyEngine` evaluates a sorted
list of `Policy` predicates against a request `context: dict`:

- First non-NEUTRAL effect wins.
- Default: `ALLOW` (fails-open). Operators wire deny policies to
  close that gap explicitly.

Built-in policy factories (composable, per-tenant):

```python
from core.governance import (
    PolicyEngine,
    deny_external_retrieval,
    restrict_mcp_tool_by_role,
    limit_ingestion_size,
    enforce_retention,
)

policies = PolicyEngine([
    deny_external_retrieval(priority=10),
    restrict_mcp_tool_by_role(
        priority=20,
        allowed={
            "agent": {"get_context", "query_graph", "get_module_summary"},
            "admin": {"*"},               # treat "*" as a literal tool
            "*": {"get_module_summary"},  # universal grant
        },
    ),
    limit_ingestion_size(max_bytes=10_000_000_000, priority=30),
    enforce_retention(max_age_days=365, priority=40),
])
```

Policies must be deterministic — no clock reads, no PRNG.

## Access control composition

```python
from core.governance import AccessControl, AuditLogger, TenantManager

ac = AccessControl(
    tenants=TenantManager(...),
    policies=policies,
    audit=AuditLogger(...),
)

decision = ac.check(AccessRequest(
    actor=AuditActor.AGENT,
    role="agent",
    tenant_id="acme-corp",
    repo_id="acme",
    action="retrieve",
    entity_id="u-abc...",
    entity_kind="Function",
))
if not decision.allowed:
    raise HTTPException(status_code=403, detail=decision.reason)
```

Every decision (allow OR deny) emits a hash-chained `audit_event`
with action `policy_decide`. The audit chain is the only mutable
governance state.

## Secrets handling

- `Settings.mcp_api_key` is a `SecretStr` — `repr(settings)` never
  prints the value.
- `Settings.neo4j_password` is a `SecretStr` — same protection.
- `.env*` templates ship with empty secret slots; real values come
  from the operator's secret manager.
- `docker-compose.production.yml` uses `${VAR:?required}` so a
  missing secret aborts compose-up immediately.

## Audit trail integrity

If the audit chain breaks (`/audit/verify` returns `intact: false`):

1. Treat as a **critical security event** — the chain is the
   only proof of past governance decisions.
2. Cross-check the durable JSONL sink (`JsonlFileAuditSink`) against
   any in-memory copies.
3. Replay the JSONL into a fresh `ImmutableLogStore`; verify that
   matches.
4. Quarantine any actor / tenant whose entries straddle the broken
   link until you can prove provenance.

See [16_AUDIT_AND_GOVERNANCE](16_AUDIT_AND_GOVERNANCE.md).

## Hardening checklist

- [ ] At least one owner account registered before exposing to users
- [ ] `SESSION_TTL_SECONDS` tuned for your environment (default 86400)
- [ ] `MCP_API_KEY` set in production
- [ ] `STRICT_BOOTSTRAP=true` in production
- [ ] Reverse proxy terminates TLS + adds rate limits
- [ ] All four backends behind a private network (no public ingress)
- [ ] Postgres user `memory-cl` has minimum privileges (no superuser)
- [ ] Neo4j password rotated from default `memory-cl-dev`
- [ ] Audit JSONL written to immutable cold storage (S3 object lock / GCS retention)
- [ ] Backups tested quarterly (restore drill)
- [ ] OTEL endpoint segregated by environment

## What this layer does NOT cover

- **Data residency** — pin tenant repos to specific shards via the
  `ShardManager` for jurisdictional compliance.
- **Field-level encryption at rest** — currently not implemented.
  Postgres + Neo4j + Qdrant rely on disk-level encryption.
- **Per-tool RBAC at the tool's data layer** — Phase 11+ may layer
  per-entity ACLs on top of the tenant gate.

---

Next: [23 — Performance + Scaling](23_PERFORMANCE_AND_SCALING.md)
