# 26 · Glossary

← back to [index](00_INDEX.md)

Single source of truth for vocabulary. If a term in the code or docs
isn't defined here, please add it.

---

### Account linking
The process of connecting a federated (OAuth/OIDC) identity to an existing
Memory-CL user account. Linking is done by **verified email**: if the upstream
provider's verified email matches a user already in the system, the new
`federated_identities` row is attached to that user — no new account is created.
Providers that supply an unverified email are refused. See [22](22_SECURITY_AND_ACCESS_CONTROL.md).

### API token
A named, revocable authentication credential (the `api_tokens` table). Issue
many — one per agent/machine — and revoke any individually without rotating
the shared `MCP_API_KEY`. Only a SHA-256 hash is stored; the raw value is shown
once. Managed via `/config/tokens` and `memcl token`.

### App config
The single-row Postgres `app_config` table holding runtime-settable config
(MCP key, OpenAI key, embedding mode, webhook secret). Read via `RuntimeConfig`
with **Postgres-over-env** precedence — falls back to env when unset.

### Audit chain
Append-only, hash-linked log of every governance / MCP / policy
decision. Each entry's `hash = SHA256(prev_hash || canonical_json(payload))`.
See [16](16_AUDIT_AND_GOVERNANCE.md).

### `audit_event`
The mandated structured-log shape for every chain entry. Fields:
`event, phase=phase_8, actor, action, entity_id, before_hash,
after_hash, tenant_id, timestamp, metadata`.

### Backpressure
The Phase-7 throttling controller that escalates from no-throttle →
ingestion → +retrieval → +MCP. **Never throttles the graph layer.**
See [14](14_DISTRIBUTED_SYSTEM.md).

### BFS (graph)
Breadth-first search via `GraphRetriever`. Visit-on-pop semantics;
sorted seeds + sorted neighbors → deterministic candidate order.

### Boot sequence
Phase-9's deterministic 8-stage health gate: storage → schema → graph
+ vector → ingestion → retrieval → MCP → audit → API exposure.
See [21](21_DEPLOYMENT.md).

### Canonical JSON
`json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
Used for hashing, audit chain entries, dense records, snapshot IDs.

### Channel (retrieval)
One of `vector | graph | metadata`. Each is a `core/retrieval/*_retriever.py`
module. Results from all three are fused per `unit_id` before ranking.

### Checksum
SHA-256 over a unit's `content` (`source_sha`) or over a versioned
schema's content fields (`compute_checksum()`). Used by Phase-8
`ChecksumVerifier` to detect on-disk corruption.

### Compaction
Phase-6 lifecycle decision to fold low-priority units into a per-module
`DenseModule` summary (memory) or merge low-centrality leaves into a
module aggregate (graph). **Plan only** — never destructive.

### `ContextEntry` / `ContextEntryType`
The atom of a `ContextPacket`. Type ∈
`constraint | risk | architecture | logic | code` (priority order).

### `ContextPacket`
The mandated retrieval output per RETRIEVAL_SYSTEM_SPEC. Carries
`task, context, risks, constraints, changes, confidence`.

### Dense notation
The Phase-3 token-optimized JSON format for module / API / graph-slice
summaries. Max key length 5 chars, sorted, drop-empty serializer.

### `DenseEncoder` / `DenseRecord`
The Phase-3 dense projection of an `IngestionUnit` per the `t / id /
dep / api / risk / file / evt` schema.

### Dependency direction
The architecture rule: `apps → core → storage → schemas`. Reverse
imports are forbidden. See [02](02_ARCHITECTURE.md).

### Determinism
Same input + same state → byte-identical output. The primary
invariant. See [25](25_DESIGN_DECISIONS.md) D-1.

### Drift
Phase-8 `EmbeddingDriftDetector` outputs three classes: embedding
cosine shift, ranking Jaccard distance, cross-shard graph
divergence. Severity bands: `LOW / MEDIUM / HIGH / CRITICAL`.

### EDGE_RULES
The (`src_kind`, `edge_kind`, allowed_dst_kinds) table in
`schemas/graph.py`. Every edge passes `is_edge_allowed()` before
write. See [11](11_GRAPH_SYSTEM.md).

### EXTERNAL (node)
`NodeKind.EXTERNAL`. Materialized for unresolved imports / calls /
bases. Skipped by retrieval, dimmed in the UI, surfaced by
`get_risks`. See [11](11_GRAPH_SYSTEM.md).

### Embedding mode
The embedding provider choice: `openai` (text-embedding-3-small, 1536-dim,
needs a key) or `local` (on-device fastembed bge-small, 384-dim, no key).
Set at runtime via `POST /config/embedding-mode`; switching rebuilds + re-embeds.

### Federated identity
A user account binding to an external identity provider (GitHub, Google,
Microsoft, or generic OIDC). Stored in the `federated_identities` table as a
`(provider_id, subject)` pair linked to a Memory-CL User. A single user can
have multiple federated identities (one per provider). See [22](22_SECURITY_AND_ACCESS_CONTROL.md).

### Freshness
Auto-reingest so the memory tracks the code. A filesystem **watcher** keeps
local (mounted) repos fresh; **polling** keeps managed (git-URL) repos fresh; a
signature-verified git push **webhook** (`/webhooks/git`) triggers it instantly.

### Feature weights
The mandated Phase-4 ranking constants:
`semantic=0.35 / graph=0.25 / recency=0.20 / importance=0.15 / feedback=0.05`.
Sum must equal 1.0. See [10](10_RANKING_ENGINE.md).

### Hybrid retrieval
Phase-4's parallel fan-out across the three channels with failure
isolation. See [09](09_RETRIEVAL_SYSTEM.md).

### Ingestion
Phase-2 pipeline: walk → parse → graph build → write to Postgres +
Neo4j + Qdrant. See [03](03_DATA_FLOW.md).

### `IngestionUnit`
The atomic AST extraction output. One per
module / class / function / method / constant. Identified by a
deterministic `unit_id`. See [11](11_GRAPH_SYSTEM.md).

### Lifespan
The FastAPI startup/shutdown context manager
(`apps/api/lifespan.py`). Runs the boot sequence + wires
`AppState`.

### Lite mode
The no-Docker deployment: `MODE=lite` swaps the server stack
(Postgres/Qdrant/Neo4j/Redis) for embedded SQLite + brute-force numpy vector
search + Python-BFS graph, all in one process under `~/.memcl`. Start with
`memcl serve`. Single-user, ~100k units; beyond that, the server tier.

### MCP (Model Context Protocol)
The Phase-5 agent surface. Seven mandated tools, hash-chained
audit, in-band errors. See [08](08_MCP_TOOLING.md).

### `MCP_API_KEY`
The shared secret gating `POST /mcp/tools/{name}`. Required in
production. See [22](22_SECURITY_AND_ACCESS_CONTROL.md).

### Membership
The join record between a User and an Organization, carrying a role (`owner | admin | member | viewer`). Stored in the `memberships` table.

### `memcl`
The Phase-9 console-script CLI. Six subcommands, JSON stdout,
deterministic. See [19](19_CLI_REFERENCE.md).

### Local repo / Managed repo
The two freshness source models. **Local**: code already on a mounted path
(`/repos/<name>`) that someone else keeps current; freshness via the watcher.
**Managed**: a git URL Memory-CL clones into `/managed/<id>` and keeps pulled;
freshness via polling (or the webhook).

### `node_id`
A graph node's primary key. Equal to `unit_id` for non-EXTERNAL
nodes. EXTERNAL nodes use `external:<qname>`. See [11](11_GRAPH_SYSTEM.md).

### Organization
The top-level tenant boundary for human users. Every User belongs to at least one Organization via a Membership. Stored in the `organizations` table.

### OIDC provider
An OAuth 2.0 / OpenID Connect identity provider configured in Memory-CL via
Settings → Identity. Built-in presets: GitHub, Google, Microsoft. Custom issuers
supply a `discovery_url` for `.well-known/openid-configuration` auto-discovery.
Managed via `/config/auth/providers`. See [22](22_SECURITY_AND_ACCESS_CONTROL.md).

### Phase
A discrete vertical slice of the engine. Phases 1–10 stack additively;
each ends with a green test gate.

### `point_id`
A Qdrant point's primary key. Always equals the unit's `unit_id`.
See [12](12_EMBEDDINGS_AND_COMPRESSION.md).

### Policy engine
Phase-8's deterministic rules engine. Predicates return `ALLOW |
DENY | NEUTRAL`; first non-NEUTRAL wins. See [16](16_AUDIT_AND_GOVERNANCE.md).

### PKCE (Proof Key for Code Exchange)
An OAuth 2.0 security extension (RFC 7636) that prevents authorization-code
interception attacks. Memory-CL uses S256: a random `code_verifier` is generated
at `/auth/oauth/{id}/start`, its SHA-256 hash (`code_challenge`) is sent to the
provider, and the raw verifier is returned at callback — the provider verifies the
pair. The verifier is carried in the `memcl_oauth` handshake cookie (never the URL).

### Principal
The resolved caller identity on every request: a human user+role (from a Session cookie), an agent (from an API token / MCP key), or anonymous. See [22](22_SECURITY_AND_ACCESS_CONTROL.md).

### Quarantine
Soft-flag (Redis) marking a unit as suspect. Set by Phase-8
checksum failures. Never deletes the unit. See [16](16_AUDIT_AND_GOVERNANCE.md).

### Ranking
Phase-4 module that converts fused candidates into a sorted
`RankedResult` list using the mandated formula. See [10](10_RANKING_ENGINE.md).

### `RelevanceScore`
Phase-6 lifecycle score: `0.4·usage + 0.3·recency + 0.2·centrality
+ 0.1·success`. Drives decay / refresh decisions. See [13](13_MEMORY_EVOLUTION.md).

### Repo (`repo_id`)
A multi-tenant scoping key. Every unit, edge, and vector point
carries it. Sharding is per-repo.

### `request_id`
A 16-hex-char identifier per MCP call. Surfaces in audit + logs +
spans for end-to-end tracing.

### Role
The authorization level a User holds in an Organization via their Membership: `owner` (full control) | `admin` (manage users) | `member` (read/write) | `viewer` (read-only). Fine-grained per-repo RBAC is Phase 3.

### Runtime config
The no-restart configuration layer (`core/config_runtime.RuntimeConfig` over
`app_config`). The `/config` endpoints + the `/setup` wizard mutate it live;
auth, embedder, and webhook verification read it on every request.

### Safe mode
Process-wide read-only flag controlled by
`core.safety.safe_mode.SafeModeController`. Engaged automatically on
boot failure under `STRICT_BOOTSTRAP=true`.

### `SCHEMA_VERSION`
Global string in `schemas/base.py`. Bump only via a real schema
migration. See [25](25_DESIGN_DECISIONS.md) D-11.

### Session
A server-side record created on login, bound to a browser via an httpOnly cookie. Only the SHA-256 hash of the token is stored; sessions are individually revocable and expire after `SESSION_TTL_SECONDS`. See [22](22_SECURITY_AND_ACCESS_CONTROL.md).

### Shard
A logical partition keyed by `repo_id`. Graph + vector routers use
the same hash so per-repo data co-locates. See [14](14_DISTRIBUTED_SYSTEM.md).

### Snapshot
Phase-8 content-hashed bundle of `(graph + embeddings + retrieval
config + schema version + MCP registry + state token)`. Same inputs
→ same `snapshot_id`. See [17](17_SNAPSHOT_AND_REPLAY.md).

### `source_sha`
SHA-256 of an `IngestionUnit.content`. Drives the
`ON CONFLICT WHERE source_sha differs` upsert guard.

### Tenant
The first-class ownership scope for repos. `TenantManager` enforces
single-owner-per-repo. See [22](22_SECURITY_AND_ACCESS_CONTROL.md).

### `unit_id`
A unit's logical identity:
`SHA256(repo_id ⊕ file_path ⊕ qualified_name)`. Equal across all
three stores. See [11](11_GRAPH_SYSTEM.md).

### User
A human account with an email address and an argon2id-hashed local password credential. Belongs to one or more Organizations via Memberships. Stored in the `users` + `user_credentials` tables.

### Version token
Monotonic per-tenant counter (`v0`, `v1`, …) used by Phase-7
retrieval cache invalidation and Phase-8 snapshot identity.
See [17](17_SNAPSHOT_AND_REPLAY.md).

### Worker pool
Phase-7 bounded asyncio executor with deterministic exponential
backoff retry. See [14](14_DISTRIBUTED_SYSTEM.md).

---

← back to [index](00_INDEX.md)

### Webhook (git)
`POST /webhooks/git` — receives GitHub/GitLab push events, verifies the
signature (GitHub HMAC / GitLab token) against the configured secret, and
triggers a managed repo's reingest. Rejects everything when no secret is set.
