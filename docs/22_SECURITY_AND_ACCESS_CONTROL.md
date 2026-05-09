# 22 · Security + Access Control

← back to [index](00_INDEX.md) · related: [16_AUDIT_AND_GOVERNANCE](16_AUDIT_AND_GOVERNANCE.md), [21_DEPLOYMENT](21_DEPLOYMENT.md), [08_MCP_TOOLING](08_MCP_TOOLING.md)

Three layers of access control:

1. **Auth at the network edge** — `MCP_API_KEY` for the agent surface.
2. **Tenant isolation** — `TenantManager.assert_owns_repo` everywhere.
3. **Policy engine** — deterministic deny/allow rules.

## MCP API key

`apps/mcp/auth.py::require_mcp_api_key` is the FastAPI dependency
that gates `POST /mcp/tools/{name}`.

Behavior:

- `Settings.mcp_api_key` unset → dev mode; every request allowed.
- Key set → request must present `X-API-Key: <key>` OR
  `Authorization: Bearer <key>`. Wrong / missing → HTTP 401.

The `/mcp/tools` listing endpoint is intentionally unauthenticated —
discovering the surface is cheap and cannot leak data.

```python
# in production .env
MCP_API_KEY=<rotate-me-quarterly>
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

If you need org-wide auth on every endpoint (not just MCP), terminate
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
