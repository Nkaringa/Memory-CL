# 21 · Deployment

← back to [index](00_INDEX.md) · related: [04_INSTALLATION](04_INSTALLATION.md), [22_SECURITY_AND_ACCESS_CONTROL](22_SECURITY_AND_ACCESS_CONTROL.md), [PHASE_9_DEPLOYMENT.md](PHASE_9_DEPLOYMENT.md)

The full Phase-9 deployment runbook lives in
[PHASE_9_DEPLOYMENT.md](PHASE_9_DEPLOYMENT.md). This page is the
quick-start + operational top-of-mind reference.

## Topology

| Service | Stateful? | Replicas | Notes |
|---|---|---|---|
| `api` | no | 2+ | FastAPI; auto-restart; same image as `worker` |
| `worker` | no | N | Phase-7 worker pool host (slot provisioned, ingest queue is operator extension) |
| `postgres` | yes | 1 primary + N replicas | canonical store |
| `neo4j` | yes | 1 per shard | graph; per-tenant shard placement |
| `qdrant` | yes | 1 per shard | vectors; same shard index as graph |
| `redis` | yes (ephemeral OK) | 1 cluster | lifecycle flags + cache + sessions |

API + worker are stateless — replicate freely behind any TCP load
balancer. State lives only in the four backends.

## Files

| File | Purpose |
|---|---|
| `Dockerfile.production` | Multi-stage non-root prod image, healthcheck, OTEL on |
| `docker-compose.production.yml` | Full stack with required-secret guards + restart policy |
| `.env.development` / `.env.staging` / `.env.production` | Per-env templates |
| `scripts/boot.sh` | Container preflight + exec |

## Quick start

```bash
# 1. supply real secrets to your secret store
export POSTGRES_PASSWORD=<...>
export NEO4J_PASSWORD=<...>
export MCP_API_KEY=<...>

# 2. bring up
cp .env.production .env
docker compose -f docker-compose.production.yml up -d

# 3. verify boot + readiness
curl -fsS http://localhost:8000/status   | jq '{ok: .boot_overall_ok, env: .environment, safe: .safe_mode.enabled}'
curl -fsS http://localhost:8000/health/ready | jq '.status'
```

## Secrets

`docker-compose.production.yml` uses `${VAR:?required}` guards.
Compose fails loudly if any required secret is missing — better
than silent boot with empty creds.

Required:
- `POSTGRES_PASSWORD`
- `NEO4J_PASSWORD`
- `MCP_API_KEY` (production only — see [22_SECURITY_AND_ACCESS_CONTROL](22_SECURITY_AND_ACCESS_CONTROL.md))

## Boot orchestration (Phase 9)

`apps/api/bootstrap.py::BootSequence` runs the spec-mandated 8-stage
health gate at every container startup:

| Order | Stage | Probe |
|---|---|---|
| 1 | `storage_init` | every Phase-1 client `.ping().ok` |
| 2 | `schema_validation` | Phase-8 `SchemaValidator` smoke |
| 3 | `graph_vector_validation` | shard routers + `graph_repo.neighbors` |
| 4 | `ingestion_readiness` | `units_repo` exposes upsert/list/delete |
| 5 | `retrieval_warmup` | `RankingModel` + `QueryPlanner` constructible |
| 6 | `mcp_registry` | default registry has 7 tools |
| 7 | `audit_chain` | empty audit chain `verify_chain()` returns True |
| 8 | `api_exposure` (optional) | `app.state.app_state` populated |

Outcome surfaced at `/status`. Under `STRICT_BOOTSTRAP=true`, any
required-stage failure auto-flips `SafeModeController` rather than
crashing.

## Safe mode

If safe-mode is engaged:
- read paths stay open (`/health/*`, `/retrieve`, `/mcp/tools/get_*`)
- mutating endpoints are expected to consult
  `state.safe_mode.status.enabled` and return 503 (operator wires
  this in their reverse proxy or per-route check)

Disable explicitly via `SAFE_MODE_ENABLED=false` and operator
intervention — the controller is process-wide, not auto-recovering.

## Scaling

### Vertical first

Memory-CL runs comfortably on a single node up to a few thousand
repos. Postgres + Neo4j + Qdrant + Redis on the same host with
4-8 GB each handle most workloads.

### Horizontal scale-out

When you exceed single-node capacity:

1. **Shard the graph + vector tier.** Increase `SCALE_SHARD_COUNT`,
   provision per-shard Neo4j + Qdrant. Routing is deterministic by
   `repo_id` (Phase 7).
2. **Scale the API tier.** `docker compose --scale api=N`. All
   replicas read from the same backends; lifespan-bootstrapped
   shared state (boot outcome, audit logger) is per-process — that's
   fine because the chain lives in Redis-backed sinks in production.
3. **Scale the worker tier.** Increase `replicas` in
   `docker-compose.production.yml`. Workers consume from the
   `infra/distributed` queue (operator wiring; see Phase-11).

## Rollouts

`docker-compose.production.yml` ships with:

```yaml
deploy:
  update_config:
    parallelism: 1
    order: start-first
  restart_policy:
    condition: on-failure
    max_attempts: 5
```

`start-first` guarantees the new container is healthy before the
old one is stopped. The healthcheck calls `/health/live` so a
crashed boot blocks the rollout.

## Observability

- OTEL exporters wire to `OTEL_EXPORTER_OTLP_ENDPOINT` if set.
- JSON structured logs by default (`LOG_FORMAT=json`).
- See [15_OBSERVABILITY](15_OBSERVABILITY.md) for the full picture.

## Backups

| What | Where | Frequency |
|---|---|---|
| Postgres `ingestion_units` | pg_dump or managed backup | hourly |
| Neo4j graph | neo4j-admin dump or managed backup | hourly |
| Qdrant snapshots | Qdrant native snapshot API | hourly |
| Audit chain | JSONL via `JsonlFileAuditSink` | continuous append-only |
| Redis lifecycle keys | RDB snapshot (or recompute from analytics if lost) | daily |

The Phase-8 audit chain is append-only by construction — you cannot
edit history. Ship the JSONL to immutable cold storage (S3 with
object lock, GCS with retention policy).

## Operating runbook

See the full one-page runbook in
[PHASE_9_DEPLOYMENT.md](PHASE_9_DEPLOYMENT.md) section 6.

---

Next: [22 — Security + Access Control](22_SECURITY_AND_ACCESS_CONTROL.md)
