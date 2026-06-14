# 06 · Configuration

← back to [index](00_INDEX.md) · related: [04_INSTALLATION](04_INSTALLATION.md), [21_DEPLOYMENT](21_DEPLOYMENT.md), [22_SECURITY_AND_ACCESS_CONTROL](22_SECURITY_AND_ACCESS_CONTROL.md)

All runtime configuration flows through `core.config.Settings`
(pydantic-settings). Settings are loaded from environment variables;
`.env*` files are honored when present.

## Environment templates

| File | Purpose |
|---|---|
| `.env.development` | Loose dev defaults; no auth, JSON logs off |
| `.env.staging` | Strict bootstrap; secrets pulled from CI vars |
| `.env.production` | Required-secret guards; OTEL on; UI on |

Copy one to `.env` before `docker compose up`.

## Setting reference

Grouped by phase / concern.

### Storage (Phase 1)

| Var | Default | Notes |
|---|---|---|
| `POSTGRES_URL` | `postgresql+asyncpg://postgres:postgres@postgres:5432/memory` | SQLAlchemy async URL |
| `QDRANT_URL` | `http://qdrant:6333` | gRPC also exposed on `6334` |
| `NEO4J_URI` | `bolt://neo4j:7687` | Cypher endpoint |
| `NEO4J_USER` | `neo4j` | |
| `NEO4J_PASSWORD` | `memory-cl-dev` | **MUST** override in prod |
| `REDIS_URL` | `redis://redis:6379/0` | DB 0 holds lifecycle + audit + quarantine flags |

### Embeddings

Embeddings are **live** (not a placeholder). Two providers, chosen by
`embedding_mode` — settable at runtime via `POST /config/embedding-mode`
(server default `openai`, lite default `local`):

| Var | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | empty | Required only when `embedding_mode = openai`. Usually set at runtime via `POST /config/openai-key` (stored in `app_config`, Postgres-over-env). |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI model (1536-dim). Local mode uses fastembed `BAAI/bge-small-en-v1.5` (384-dim, no key). |
| `ANTHROPIC_API_KEY` | empty | Reserved (future LLM selection). |
| `PRIMARY_LLM` | `claude-sonnet-4` | Currently informational. |

The query-side and document-side embedder MUST share model + dimension or
retrieval is noise; switching modes rebuilds collections + re-embeds. See
**[12_EMBEDDINGS_AND_COMPRESSION](12_EMBEDDINGS_AND_COMPRESSION.md)**.

### Deployment mode (lite vs server)

| Var | Default | Notes |
|---|---|---|
| `MODE` | `server` | `server` = the Docker stack (Postgres/Qdrant/Neo4j/Redis). `lite` = embedded SQLite/numpy/Python backends, no Docker (`pip install` + `memcl serve`). |
| `LITE_DATA_DIR` | `~/.memcl` | Where lite keeps `data.db` + the model cache (`~` expanded). |

### Freshness — auto-reingest (Phase 3)

| Var | Default | Notes |
|---|---|---|
| `FRESHNESS_ENABLED` | `true` | Master switch for the watcher + poller. |
| `FRESHNESS_WATCH_ENABLED` | `true` | Filesystem watcher for local (mounted) repos. |
| `FRESHNESS_POLL_INTERVAL_SECONDS` | `180` | How often managed (git-URL) repos are polled. |
| `FRESHNESS_DEBOUNCE_MS` | `3000` | Quiet window after a burst of edits before reingest. |
| `FRESHNESS_FORCE_POLLING` | `false` | Force watchfiles polling on non-inotify filesystems. |
| `MANAGED_REPOS_ROOT` | `/managed` | Writable workspace for git-cloned managed repos. |
| `LOCAL_REPOS_ROOT` | `/repos` | Mounted read-only code the watcher observes. |
| `GITHUB_TOKEN` | empty | Optional, for cloning private managed repos. |
| `WEBHOOK_SECRET` | empty | Optional fallback for git-push webhook signature verification (usually generated at `POST /config/webhook-secret/generate`). |

### Runtime config + onboarding

Beyond these env vars, a runtime-config layer (Postgres `app_config`,
**Postgres-over-env** precedence) lets operators change settings WITHOUT a
restart via the first-run wizard (`/setup`, `memcl setup`) and the `/config`
endpoints: generate/rotate the MCP key, set/clear the OpenAI key, choose the
embedding mode, generate the webhook secret, and mint **named, revocable API
tokens**. When `app_config` is empty, everything falls back to env (backward
compatible). See **[22_SECURITY_AND_ACCESS_CONTROL](22_SECURITY_AND_ACCESS_CONTROL.md)**.

### Retrieval (Phase 4)

| Var | Default | Range |
|---|---|---|
| `MAX_CONTEXT_TOKENS` | `4000` | > 0 |
| `CHUNK_SIZE` | `400` (tokens) | > 0 |
| `CHUNK_OVERLAP` | `40` | < `chunk_size` |
| `MAX_GRAPH_TRAVERSAL_DEPTH` | `3` | 1–10 |
| `DEFAULT_TOP_K` | `10` | 1–200 |

### Human auth (Phase 1 identity)

| Var | Default | Notes |
|---|---|---|
| `SESSION_TTL_SECONDS` | `86400` | Human browser session lifetime in seconds (24 h). After expiry the httpOnly cookie is rejected and the user must log in again. |
| `OAUTH_STATE_SECRET` | empty | Signing secret for the `memcl_oauth` handshake cookie used by the Phase-2 federated-login flow. Should be set in production to keep the cookie stable across restarts and instances. OAuth providers themselves are configured at runtime via Settings → Identity — no env vars are needed for them. |

### MCP (Phase 5)

| Var | Default | Notes |
|---|---|---|
| `MCP_API_KEY` | empty | If set, every `/mcp/tools/{name}` call MUST present `X-API-Key` or `Authorization: Bearer`. Required in production. |
| `MCP_SESSION_TTL_SECONDS` | `3600` | TTL on Redis session-memory keys (`update_memory` tool) |

### Lifecycle (Phase 6)

| Var | Default | Notes |
|---|---|---|
| `LIFECYCLE_DECAY_THRESHOLD_DAYS` | `30` | Idle days before decay-eligible |
| `LIFECYCLE_LOW_PRIORITY_THRESHOLD` | `0.3` | Score below = downgrade candidate |
| `LIFECYCLE_REFRESH_THRESHOLD` | `0.4` | Score below = embedding refresh |
| `LIFECYCLE_CENTRALITY_THRESHOLD` | `0.2` | In-degree band for compaction |
| `LIFECYCLE_USAGE_WINDOW_DAYS` | `14` | Usage-counter window |

### Distributed scale (Phase 7)

| Var | Default | Range |
|---|---|---|
| `SCALE_WORKER_COUNT` | `4` | 1–64 |
| `SCALE_SHARD_COUNT` | `4` | 1–256 |
| `SCALE_RETRIEVAL_CACHE_SIZE` | `1024` | entries |
| `SCALE_RETRIEVAL_CACHE_TTL_SECONDS` | `300` | s |
| `SCALE_DEFAULT_RATE_PER_SECOND` | `20` | per (caller, resource) bucket |
| `SCALE_BACKPRESSURE_THRESHOLD` | `0.8` | queue-depth ratio that triggers throttle |
| `SCALE_BATCH_MAX_SIZE` | `32` | items per micro-batch |
| `SCALE_BATCH_MAX_WAIT_MS` | `20` | flush window |

### Production safety (Phase 9)

| Var | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | One of `development` / `staging` / `production` |
| `SAFE_MODE_ENABLED` | `false` | Force-enable safe mode regardless of boot |
| `UI_ENABLED` | `true` | Mount `/ui` static inspector |
| `STRICT_BOOTSTRAP` | `false` | Boot failures auto-flip safe mode |
| `SERVICE_LABEL` | `memory-cl` | Surfaces in `/status.service` |

### Observability

| Var | Default | Notes |
|---|---|---|
| `LOG_LEVEL` | `INFO` | One of `DEBUG/INFO/WARNING/ERROR/CRITICAL` |
| `LOG_FORMAT` | `json` | `json` (prod) or `console` (dev) |
| `OTEL_ENABLED` | `true` | Set to `false` in CI to silence span dumps |
| `OTEL_SERVICE_NAME` | `memory-cl` | |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | If set, OTLP/gRPC; else console exporter |

### API

| Var | Default | Notes |
|---|---|---|
| `API_HOST` | `0.0.0.0` | Bind interface |
| `API_PORT` | `8000` | |

## Feature flags (runtime view)

`core.safety.feature_flags.FeatureFlagRegistry` projects the
following Settings fields as named flags surfaced in `/status`:

- `enable_graph_ranking`
- `enable_incremental_indexing`
- `enable_context_compression`
- `ui_enabled`
- `strict_bootstrap`

Flags are **boot-time** — runtime mutation is intentionally not
supported (would break determinism guarantees).

## Loading order

1. Process env vars (highest priority)
2. `.env` file in working directory
3. `Settings` class defaults (lowest)

`get_settings()` is `lru_cache`d; tests must clear it via
`get_settings.cache_clear()` between configurations.

## Validation

`Settings` enforces:
- `chunk_overlap < chunk_size`
- `max_graph_traversal_depth ∈ [1, 10]`
- `mcp_api_key` is a `SecretStr` (never logged in `repr`)
- All `lifecycle_*` thresholds in `[0, 1]`

Bad values fail fast at process start.

---

Next: [07 — API Reference](07_API_REFERENCE.md)
