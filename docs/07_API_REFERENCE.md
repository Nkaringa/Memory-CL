# 07 · API Reference

← back to [index](00_INDEX.md) · related: [03_DATA_FLOW](03_DATA_FLOW.md), [08_MCP_TOOLING](08_MCP_TOOLING.md), [20_SDK_GUIDE](20_SDK_GUIDE.md)

All endpoints are JSON over HTTPS, mounted on `apps/api/main.py`. The
typed contract for every payload lives in `schemas/` (server-side
Pydantic) and `ui/lib/types.ts` (client-side TypeScript).

OpenAPI is auto-generated at `/openapi.json`; interactive docs at
`/docs` (Swagger) and `/redoc`.

## Determinism contract

Every response includes one or more of:
- `request_id` (MCP) — stable per-call
- `query_id` (retrieve) — `sha256(repo_id ⊕ text)[:16]`
- `latency_ms` — wall-clock
- `schema_version` — derived from `schemas/base.SCHEMA_VERSION`

Same input + same backend state → byte-identical body modulo
`latency_ms` (test-asserted by golden gates).

---

## Health

### `GET /health/live`

Process liveness. Does NOT touch storage.

**200 →**
```json
{ "schema_version": "1", "status": "ok", "service": "memory-cl" }
```

### `GET /health/ready`

Pings every storage backend in parallel. Returns 200 if all OK,
**503** otherwise.

**200 →**
```json
{
  "schema_version": "1",
  "status": "ok",
  "components": [
    {"name": "neo4j",    "status": "ok", "latency_ms": 1.234, "error": null},
    {"name": "postgres", "status": "ok", "latency_ms": 0.876, "error": null},
    {"name": "qdrant",   "status": "ok", "latency_ms": 2.103, "error": null},
    {"name": "redis",    "status": "ok", "latency_ms": 0.412, "error": null}
  ]
}
```

`components[]` is alphabetical by `name`.

---

## Status (Phase 9)

### `GET /status`

Full production posture in one call. Used by `/dashboard`, the CLI
`status` command, and external monitors.

**200 →**
```json
{
  "service": "memory-cl",
  "environment": "development",
  "safe_mode": {"enabled": false, "reason": "", "triggered_by": ""},
  "feature_flags": [
    {"name": "ui_enabled", "description": "...", "enabled": true},
    ...
  ],
  "boot_overall_ok": true,
  "boot_failed_stages": [],
  "boot_degraded_stages": [],
  "boot_stages": [
    {"name": "storage_init", "order": 1, "status": "ok", "error": ""},
    ...
  ],
  "mcp_tool_count": 14,
  "schema_version": "1"
}
```

---

## Ingestion

### `POST /ingest`

Triggers Phase-2 `IngestionPipeline` (parse + graph build + 3-store
write + reconciliation).

**Request**
```json
{ "repo_id": "acme", "repo_path": "/abs/path/to/repo", "commit_sha": "deadbeef" }
```

**400** — `repo_path` does not exist or isn't a directory.

**200 →**
```json
{
  "repo_id": "acme",
  "commit_sha": "deadbeef",
  "units_collection": "repo:acme",
  "metrics": {
    "files_walked": 4, "files_parsed": 4, "files_failed": 0,
    "units_emitted": 18, "units_changed": 18,
    "nodes_written": 24, "edges_written": 30, "vector_payloads_written": 18,
    "duration_ms": 12.34
  },
  "failed_files": []
}
```

Idempotency: re-running the same call on the same content is a no-op
(per-unit `source_sha` guard); `units_changed` will be 0.

---

## Retrieval

### `POST /retrieve`

Hybrid retrieval → ranking → context assembly.

**Request**
```json
{
  "text": "auth flow",
  "repo_id": "acme",
  "top_k": 5,
  "unit_kinds": ["fn", "cls"],   // optional filter
  "seed_unit_ids": []             // optional graph seeds
}
```

**200 →**
```json
{
  "query_id": "0a1b2c3d4e5f6789",
  "repo_id": "acme",
  "packet": {
    "schema_version": "1",
    "task": "auth flow",
    "context": [
      {
        "id": "<unit_id>",
        "type": "logic",
        "score": 0.342,
        "data": {
          "qualified_name": "pkg.services.auth.login",
          "file_path": "pkg/services/auth.py",
          "kind": "fn",
          "channels": ["vector"]
        }
      }
    ],
    "risks": [], "constraints": [], "changes": [],
    "confidence": 0.31
  },
  "graph_hits": 0, "vector_hits": 5, "metadata_hits": 0,
  "final_candidates": 5, "ranked_count": 5,
  "failed_channels": [],
  "latency_ms": 7.4
}
```

See [09_RETRIEVAL_SYSTEM](09_RETRIEVAL_SYSTEM.md) and
[10_RANKING_ENGINE](10_RANKING_ENGINE.md) for semantics.

**Validation** — `extra="forbid"`; unknown fields → 422.

---

## MCP

### `GET /mcp/tools`

Lists every registered tool. Public (no auth required).

**200 →**
```json
{
  "tools": [
    {"name": "search_code",         "request_schema": "SearchCodeRequest"},
    {"name": "read_unit",           "request_schema": "ReadUnitRequest"},
    {"name": "read_file",           "request_schema": "ReadFileRequest"},
    {"name": "explore",             "request_schema": "ExploreRequest"},
    {"name": "find_symbol",         "request_schema": "FindSymbolRequest"},
    {"name": "list_repos",          "request_schema": "ListReposRequest"},
    {"name": "repo_overview",       "request_schema": "RepoOverviewRequest"},
    {"name": "get_context",         "request_schema": "GetContextRequest"},
    {"name": "get_module_summary",  "request_schema": "GetModuleSummaryRequest"},
    {"name": "get_related_components", "request_schema": "GetRelatedComponentsRequest"},
    {"name": "get_risks",           "request_schema": "GetRisksRequest"},
    {"name": "query_graph",         "request_schema": "QueryGraphRequest"},
    {"name": "ingest_repository",   "request_schema": "IngestRepositoryRequest"},
    {"name": "update_memory",       "request_schema": "UpdateMemoryRequest"}
  ]
}
```
All 14 registered tools (`apps/mcp/registry.py`). See **[08_MCP_TOOLING](08_MCP_TOOLING.md)**
for what each wraps.

### `POST /mcp/tools/{name}`

Invoke a registered tool. Auth required iff `MCP_API_KEY` is set.

**Headers**
- `X-API-Key: <key>`  *or*  `Authorization: Bearer <key>` — required in prod.

**Body** — the tool's `request_schema`. See [08_MCP_TOOLING](08_MCP_TOOLING.md).

**Always 200** — failures are conveyed in-band via `status: "failed"`
plus an `error_code`.

**200 →**
```json
{
  "schema_version": "1",
  "tool": "get_context",
  "request_id": "abc1234567890def",
  "status": "success",
  "data": { /* tool-specific */ },
  "error": null,
  "error_code": null,
  "latency_ms": 6.2
}
```

**Error codes** — `validation_error`, `unauthorized`, `unknown_tool`,
`backend_error`, `internal_error`.

**401** — only when `MCP_API_KEY` is set and the request omits or
mismatches the key.

---

## Human auth — federated login (Phase 2)

`apps/api/routers/auth_oauth.py`. Handles the OAuth / OIDC browser flow and
returns a `memcl_session` cookie on success.

| Method · path | Auth required | Notes |
|---|---|---|
| `GET /auth/providers` | Public | Returns the list of **enabled** providers for login-button rendering. Never exposes secrets. |
| `GET /auth/oauth/{provider_id}/start` | None | Redirects browser to the upstream provider; sets the `memcl_oauth` handshake cookie (state + PKCE + nonce). |
| `GET /auth/oauth/{provider_id}/callback` | None (browser redirect) | Validates state/PKCE/nonce; links or creates account; sets `memcl_session` and redirects to UI. Returns 400 if the provider supplies an unverified email. |

`apps/api/routers/config.py` (admin panel — OAuth provider management):

| Method · path | Auth required | Notes |
|---|---|---|
| `POST /config/auth/providers` | Admin / bootstrap-open | Create a provider (disabled by default). Body: `{type, provider_id, client_id, client_secret, scopes, discovery_url}`. |
| `GET /config/auth/providers` | Admin / bootstrap-open | List all providers (masked — `client_secret` shown as `has_secret: bool`). |
| `PATCH /config/auth/providers/{id}` | Admin / bootstrap-open | Update client-id, secret, scopes, or discovery URL. |
| `POST /config/auth/providers/{id}/enable` | Admin / bootstrap-open | Enable (or disable) a provider. Toggle after registering the callback URL at the upstream IdP. |
| `DELETE /config/auth/providers/{id}` | Admin / bootstrap-open | Delete a provider and its federated identities. |

---

## Config + onboarding (runtime, no restart)

`apps/api/routers/config.py`. All read state from / write to `app_config`
(Postgres-over-env). Mutations are bootstrap-open until a key is configured,
then require it.

| Method · path | Purpose |
|---|---|
| `GET /config` | Onboarding/runtime state (masked — never raw keys): `configured`, `embedding_mode`, `embeddings_enabled`, `has_openai_key`, `has_webhook_secret`, `mcp_key_hint`. |
| `POST /config/mcp-key/generate` · `…/rotate` | Mint / rotate the MCP key (returned once). |
| `POST /config/openai-key` | Set/clear the OpenAI key. |
| `POST /config/embedding-mode` | `openai`/`local`; on change, rebuilds + re-embeds collections. |
| `POST /config/webhook-secret/generate` | Mint the git-webhook signing secret (once). |
| `POST /config/tokens` · `GET /config/tokens` · `DELETE /config/tokens/{id}` | Mint (once) / list (masked) / revoke named API tokens. |
| `POST /config/complete-onboarding` | Mark the first-run wizard done. |

## Freshness — auto-reingest (`apps/api/routers/freshness.py`)

| Method · path | Purpose |
|---|---|
| `GET /freshness` | All registered repos + freshness state. |
| `POST /freshness/managed` | Add a managed git-URL repo (clone + ingest + keep fresh). |
| `POST /freshness/{repo_id}/toggle` | Pause/resume watching. |
| `POST /freshness/{repo_id}/sync` | Force a freshness check now. |
| `DELETE /freshness/{repo_id}` | Deregister (delete a managed clone). |

## Webhooks

| Method · path | Purpose |
|---|---|
| `POST /webhooks/git` | GitHub/GitLab push events, signature-verified; triggers a managed repo's reingest. |

## Re-embed

| Method · path | Purpose |
|---|---|
| `POST /ingest/reembed {repo_id}` | Backfill real vectors for a repo's stored units (auth required). |

## Snapshot + Replay (Phase 9)

### `POST /snapshot/build`

Build a content-hashed snapshot of the current process-local view.

**Request**
```json
{ "tenant_id": "acme-corp", "state_version_token": "v0" }
```

**200 →**
```json
{
  "snapshot_id": "<sha256-hex>",
  "tenant_id": "acme-corp",
  "captured_at": "2026-05-08T12:34:56+00:00",
  "components": {
    "graph_state_hash": "...",
    "embedding_index_hash": "...",
    "retrieval_config_hash": "...",
    "schema_version": "1",
    "mcp_registry_hash": "...",
    "state_version_token": "v0"
  }
}
```

### `POST /snapshot/replay`

Verify a payload against a snapshot via deterministic JSON hashing.

**Request**
```json
{
  "snapshot_id": "<id>",
  "payload":         { "any": "value" },
  "expected_output": { "any": "value" }
}
```

**200 →**
```json
{
  "snapshot_id": "<id>",
  "matches": true,
  "expected_hash": "...",
  "actual_hash": "...",
  "notes": ""
}
```

**400** — `payload` field missing.

---

## Audit (Phase 9 surface over Phase 8)

### `GET /audit/tail?limit=50`

Returns the most recent N audit chain entries, oldest first within
the window.

**200 →**
```json
{
  "chain_length": 132,
  "entries": [
    {
      "seq": 130,
      "prev_hash": "...",
      "hash": "...",
      "payload": {
        "event": "audit_event",
        "phase": "phase_8",
        "actor": "agent",
        "action": "policy_decide",
        "entity_id": "u-abcdef…",
        "tenant_id": "acme",
        "timestamp": "...",
        "before_hash": "...", "after_hash": "...",
        "metadata": { ... }
      }
    }
  ]
}
```

### `GET /audit/verify`

Re-walk the chain from genesis. Reports first broken link if any.

**200 →**
```json
{ "chain_length": 132, "intact": true, "error": "", "broken_at_seq": null }
```

A non-intact chain still returns 200 — the body carries `intact: false`,
`broken_at_seq`, and an `error` message. Treat HTTP 200 + `intact:false`
as a critical alert.

---

## Cross-cutting

### Auth

- Most endpoints are unauthenticated in dev.
- `POST /mcp/tools/{name}` is gated behind `MCP_API_KEY` when set.
- Production: enforce via reverse proxy (nginx) for full coverage if
  you need org-wide auth on every route.

### Errors (server-side, not in-band)

| Code | Cause |
|---|---|
| 400 | Bad request body (e.g. `repo_path` missing on /ingest) |
| 401 | Missing / invalid `X-API-Key` for MCP routes |
| 422 | Pydantic validation failure (`extra="forbid"`) |
| 503 | `/health/ready` when a backend is down |

In-band MCP errors return **200** with `status: "failed"`. This is the
spec — clients route on `status`, not HTTP code.

---

Next: [08 — MCP Tooling](08_MCP_TOOLING.md)
