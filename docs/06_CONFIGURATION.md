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

### LLM / embedding (Phase 1+, future)

| Var | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | empty | Reserved; Phase-3 ships a deterministic embedder |
| `ANTHROPIC_API_KEY` | empty | Reserved |
| `EMBEDDING_MODEL` | `text-embedding-3-large` | Currently informational |
| `PRIMARY_LLM` | `claude-sonnet-4` | Currently informational |

### Retrieval (Phase 4)

| Var | Default | Range |
|---|---|---|
| `MAX_CONTEXT_TOKENS` | `4000` | > 0 |
| `CHUNK_SIZE` | `400` (tokens) | > 0 |
| `CHUNK_OVERLAP` | `40` | < `chunk_size` |
| `MAX_GRAPH_TRAVERSAL_DEPTH` | `3` | 1–10 |
| `DEFAULT_TOP_K` | `10` | 1–200 |

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
