# Full Identity & Auth — Design Spec

**Date:** 2026-06-13
**Status:** Approved (design); pending implementation plan
**Topic:** Multi-org human identity, federated + local authentication, sessions, and RBAC for Memory-CL — available across all tiers (lite/indie, team, enterprise).

---

## 1. Goal

Add a complete, durable identity layer to Memory-CL so **humans** authenticate (local password **or** federated OIDC/OAuth), belong to **organizations** and **teams**, and are authorized by **role + per-repo grants** — while **agents** keep using API tokens unchanged. Build it once so every product tier has auth.

## 2. Why now / what exists

Grounding (see `Explore` substrate map) found a solid governance substrate but **no identity**:

- ✅ `TenantManager` (repo ownership, `assert_owns_repo`, `CrossTenantAccessError`) — but **in-memory, resets on restart**.
- ✅ `PolicyEngine` / `AccessControl.check()` / `AccessRequest` — **built but NOT enforced on REST routers** (only MCP tools + audit use it).
- ✅ Named revocable API tokens (`api_token_repo` + `TokenCache`), legacy MCP key, Postgres-over-env runtime config, full lite-SQLite parity.
- ❌ No `User`, `Session`, password auth, OAuth/OIDC, RBAC beyond `restrict_mcp_tool_by_role`.
- ⚠️ **Critical constraint:** today `tenant_id = scope_marker(api_key)`. Tenancy is welded to the credential. Full auth requires making tenancy **durable** and resolving it from a **request-scoped principal** instead.

## 3. Core model

**Organization = the durable Tenant.** We give `TenantManager` Postgres + SQLite backing (a known Phase-9 gap) and treat a Tenant as an Org. Repos already belong to tenants, so ownership wiring survives.

New entities — each a Protocol with a Postgres impl (`storage/*`) and a lite-SQLite impl (`storage/lite/*`), following the `api_token_repo` pattern:

| Entity | Key fields | Notes |
|---|---|---|
| **User** | `user_id`, `email` (unique, normalized), `display_name`, `avatar_url`, `status` (active/suspended), `created_at` | Global; may join many orgs. |
| **Credential** | local: `user_id`, `password_hash` (argon2id); federated: `user_id`, `provider`, `subject` (`sub`), `email_verified` | `UNIQUE(provider, subject)`. Linking only on **verified** email. |
| **Organization** (=Tenant) | `org_id` (== tenant_id), `name`, `slug`, `created_at` | Durable. Owns repos. |
| **Membership** | `user_id`, `org_id`, `role` ∈ {owner, admin, member, viewer}, `status` (active/invited), `invited_by`, `joined_at` | Org-level RBAC. |
| **Team** | `team_id`, `org_id`, `name`, `slug` | Sub-group. |
| **TeamMembership** | `team_id`, `user_id` | Must be org member. |
| **RepoGrant** | `org_id`, `repo_id`, subject (`team_id` or `user_id`), `access` ∈ {read, write, admin} | Per-repo RBAC. |
| **Invitation** | `org_id`, `email`, `role`, `team_ids`, `token_hash`, `expires_at`, `status` | Email invite; accept → Membership. |
| **Session** | `session_id`, `user_id`, `active_org_id`, `csrf_token`, `created_at`, `expires_at`, `revoked_at` | Durable table + in-memory `SessionCache`. |
| **AuthProvider** (config) | `type` (github/google/microsoft/oidc), `name`, `client_id`, `client_secret` (SecretStr), `discovery_url`, `scopes`, `enabled` | Runtime config, Postgres-over-env. |

### Roles & access resolution

- **owner** — full control incl. delete org, manage admins/billing. First registrant = owner of bootstrap org.
- **admin** — manage members, teams, providers, repos, grants. Can't delete org.
- **member** — use repos granted via team/user grants; ingest/retrieve per grant level.
- **viewer** — read-only retrieval; no ingest/mutation.

owner/admin → all org repos implicitly. member/viewer → only granted repos (via team or direct grant).

## 4. The convergence point — `Principal`

Every credential path resolves to one request-scoped object, then hits the **existing** `AccessControl.check()` (wired into routers, which today don't enforce it):

```python
@dataclass(frozen=True, slots=True)
class Principal:
    kind: Literal["user", "agent"]
    user_id: str            # "agent" for token/legacy paths
    org_id: str             # active org (tenant_id)
    email: str
    roles: tuple[str, ...]  # org role(s); maps to AccessRequest.role
    is_authenticated: bool
```

`get_principal` dependency accepts, in order: **session cookie** (humans) → **API token** (agents, PR #33) → **legacy MCP key** (synthetic `agent` Principal in the default org). No existing flow regresses; the legacy key keeps working.

Routers gain a guard that builds an `AccessRequest` from the `Principal` and calls `ac.check()`; deny → HTTP 403 + audit event.

## 5. Authentication flows

- **Local:** `POST /auth/register` (bootstrap-open for first user, then invite-gated), `POST /auth/login` (argon2id verify → create session → httpOnly+Secure+SameSite=Lax cookie + CSRF token), `POST /auth/logout` (revoke session), `GET /auth/me` (current Principal).
- **Federated (authlib):** `GET /auth/{provider}/start` → redirect with **state + PKCE + nonce** → `GET /auth/{provider}/callback` → code exchange → validate `id_token`/userinfo → find-or-link `FederatedIdentity` (by verified email) → session.
- **One generic OIDC/OAuth2 engine**; GitHub/Google/Microsoft are presets (endpoints + default scopes). Generic OIDC uses `discovery_url`.

## 6. Provider config — why client-IDs aren't a blocker

`auth_providers` rows are managed at runtime via Settings → Identity and `POST/GET /config/auth/providers` (+ enable/disable), same Postgres-over-env, no-restart pattern as the MCP key. We build and test the entire engine now against **local accounts + a mock OIDC server**; real GitHub/Google/MS client-id+secret get pasted later and each provider toggled on — **no code change, no redeploy logic**.

## 7. Sessions & lite parity

- `sessions` table (Postgres) + lite SQLite mirror; in-memory `SessionCache` for O(1) validation (mirrors `TokenCache`); TTL from a new `SESSION_TTL_SECONDS` (default e.g. 86400), revocation on logout/suspend/admin action.
- **Lite mode** auto-creates a default org + first owner on first boot; OIDC optional. Every new repo gets a `storage/lite/*` impl so lite stays first-class.

## 8. Security

argon2id hashing; PKCE+state+nonce on OAuth; httpOnly+Secure+SameSite cookies + CSRF token on mutations; account linking only on verified email; `SecretStr` for client secrets & hashes (never logged); login rate-limit/backoff; session revocation. Login/logout/role-change/invite/grant emit into the **existing hash-chained audit log** (`AuditActor.USER`). Auth tables are explicitly **outside** the determinism/snapshot golden contract (random salts, timestamps).

## 9. Coexistence with agents

Agents keep using API tokens (unchanged). A token MAY be associated with a user/org for attribution. Humans use sessions. Both → `Principal` → `AccessControl`. MCP/native-transport auth (`token_auth.credential_accepted`) is extended to also accept a valid session where a browser calls MCP routes.

## 10. New dependencies

`authlib` (OIDC/OAuth2 + PKCE), `argon2-cffi` (password hashing), `itsdangerous` (signed cookies/CSRF); elevate `httpx` to a runtime dep.

## 11. Tier mapping ("auth for every tier")

- **Lite / indie** — local accounts, single auto-created org, SQLite session store. OIDC optional.
- **Team** — orgs + teams + invitations + GitHub/Google.
- **Enterprise** — Entra ID + generic OIDC (Okta/Auth0/Keycloak/Authentik); SCIM/SSO-provisioning is a later add-on. Audit already present.

## 12. Build phasing (one spec → three shippable layers)

1. **Identity core** — durable orgs (Tenant backing), `users`/`sessions`, local argon2 login, `Principal` + `get_principal`, router enforcement via `ac.check()`, login page + auth guard in UI. *Ships working single-org local auth.*
2. **Federation** — authlib engine, `auth_providers` runtime config + `/config/auth/*`, GitHub/Google/Microsoft/generic presets, account linking, Settings → Identity provider UI. *Client-IDs land here.*
3. **Orgs · teams · RBAC** — teams, memberships, invitations, per-repo grants, org switcher, admin UI for users/teams/grants.

## 13. Non-goals (this milestone)

SCIM auto-provisioning, SAML (OIDC only), billing/subscriptions, field-level encryption at rest, per-entity ACLs below the repo grain, password-reset email delivery infra (token generated; SMTP wiring is operator-config later).

## 14. Back-compat & migration

- Legacy MCP key + API tokens: unchanged, keep working (synthetic agent Principal, default org).
- Durable `TenantManager`: a migration seeds a `default` org and assigns existing repos to it; existing single-tenant deployments are unaffected.
- Server mode untouched in behavior until an operator creates the first user.

---

## Open implementation decisions (locked defaults, override in plan if needed)

- Session store = **durable table + cache** (not Redis-only) for revocation + lite parity. *Locked.*
- Org↔user = **many-to-many via Membership** (full multi-org). *Locked (per user request).*
- Password reset = token generated + endpoint; **email delivery deferred** to operator SMTP config. *Locked.*
