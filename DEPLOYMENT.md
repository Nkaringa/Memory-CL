# Deployment

Operator guide for deploying Memory-CL into a production environment
on infrastructure you already control. This document covers the
**deployable artifact** — image build, env contract, compose stack —
not cloud-provider provisioning.

## Cross-references

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — what the running system looks like
- **[RUNBOOK.md](RUNBOOK.md)** — incident response, on-call procedures
- **[SECURITY.md](SECURITY.md)** — threat model, access controls
- **[docs/21_DEPLOYMENT.md](docs/21_DEPLOYMENT.md)** — the deeper deployment narrative

---

## 1. Artifact

| Image | Source | Purpose |
|---|---|---|
| `memory-cl:prod` | `Dockerfile.production` | API + worker (Python) |
| `memory-cl-ui:prod` | `ui/Dockerfile.production` | Next.js standalone UI |

Both images:

- run as **uid 1000** (non-root); container filesystems should be
  `read_only: true` in production with explicit tmpfs mounts.
- have **stdlib-only health probes** — no `curl`, no extra binaries.
- ship from a **deterministic builder stage** (multi-stage build,
  pinned base image digest *should* be added once your registry
  policy requires it).
- are reaped by **tini** as PID 1 so SIGTERM cleanly drains workers.

### Reproducible builds

Direct dependencies are pinned in [`requirements.lock.txt`](requirements.lock.txt).
The Dockerfile installs with `pip wheel --no-deps` from that lockfile
and refuses to fall back to the network at runtime install time
(`--no-index --find-links=/wheels`).

For full hash-pinned reproducibility, regenerate the lockfile with
pip-tools:

```bash
pip install pip-tools==7.4.1
pip-compile --generate-hashes \
    --output-file=requirements.lock.txt \
    pyproject.toml
```

The Dockerfile already supports the resulting hashed file unchanged.

---

## 2. Environment contract

All environment variables for the runtime are documented in
[`.env.example`](.env.example). The three real environments are
discriminated by `ENVIRONMENT={development,staging,production}`.

| Variable | dev | staging | prod |
|---|---|---|---|
| `ENVIRONMENT` | `development` | `staging` | `production` |
| `LOG_FORMAT` | `console` | `json` | `json` (enforced) |
| `LOG_LEVEL` | `DEBUG` | `INFO` | `INFO` |
| `OTEL_ENABLED` | `false` | `true` | `true` (enforced) |
| `STRICT_BOOTSTRAP` | `false` | `true` | `true` (enforced) |
| `MCP_API_KEY` | optional | required | required + non-sentinel |
| `NEO4J_PASSWORD` | dev sentinel ok | required + non-sentinel | required + non-sentinel |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (console exporter) | recommended | recommended |

### Strict validation

`Settings._enforce_environment_contract` (Pydantic `model_validator`)
enforces the matrix above on process start. Booting in production
with a leftover dev sentinel (e.g. `NEO4J_PASSWORD=memory-cl-dev`)
raises `StrictConfigError` *before* the API binds the port — the
container exits non-zero and the orchestrator restarts it. There is
no path that ships unauthenticated traffic with a sentinel password.

### Secrets management

`.env.production` is a **template** — every secret-bearing line is
empty. Real values come from your secret manager (Vault, AWS Secrets
Manager, GCP Secret Manager, etc.) and are injected via either:

1. `docker compose --env-file <ephemeral-file>`, or
2. `secrets:` blocks if you switch to Compose with secrets, or
3. environment injection from your orchestrator (preferred in
   Kubernetes / Nomad).

`docker-compose.production.yml` uses `${VAR:?required}` guards on
every secret variable, so a missing value fails the `up` command
loudly instead of starting a half-configured stack.

---

## 3. Local production simulation

```bash
# 1. Populate .env.production with real values (or export to env).
# 2. Bring the full stack online:
docker compose -f docker-compose.production.yml up -d

# 3. Wait for boot + verify posture:
curl -fsS http://localhost:8000/health/live
curl -fsS http://localhost:8000/health/ready    | jq .
curl -fsS http://localhost:8000/health/dependencies | jq .
curl -fsS http://localhost:8000/status          | jq .
```

The compose file brings up:

| Service | Image | Role |
|---|---|---|
| `api` | `memory-cl:prod` | FastAPI (2 replicas, rolling update) |
| `ui` | `memory-cl-ui:prod` | Next.js standalone UI |
| `worker` | `memory-cl:prod` (different ENTRYPOINT) | Async worker pool |
| `postgres` | `postgres:16-alpine` | Metadata + ingestion units |
| `qdrant` | `qdrant:v1.11.0` | Vector store |
| `neo4j` | `neo4j:5.22-community` | Graph store |
| `redis` | `redis:7-alpine` | Cache + version tokens |

Healthchecks gate `depends_on`, so the API only starts after all
storage backends report healthy.

---

## 4. Boot sequence

`scripts/boot.sh` (the container ENTRYPOINT) verifies process-level
prerequisites, then `exec`s into uvicorn. Inside the running app,
[`apps/api/bootstrap.py::BootSequence`](apps/api/bootstrap.py) runs the
deterministic 8-stage health gate at lifespan start-up:

```
1. storage_init           — every backend client connects
2. schema_validation      — SchemaValidator smoke check
3. graph_vector_validation — Neo4j constraints + Qdrant routability
4. ingestion_readiness    — Postgres ingestion_units table present
5. retrieval_warmup       — RankingModel + retrievers constructible
6. mcp_registry           — registry exposes ≥7 tools
7. audit_chain            — ImmutableLogStore reachable + verifies clean
8. api_exposure           — FastAPI router registration (non-required)
```

Outcomes are surfaced at `/status` (per-stage `ok`/`degraded`/`failed`).

If `STRICT_BOOTSTRAP=true` and any **required** stage fails or two+
stages degrade, the [SafeModeController](core/safety/safe_mode.py)
flips the process into `read_only` mode automatically. See
[RUNBOOK.md](RUNBOOK.md#degraded-startup) for recovery.

---

## 5. Health surface

| Endpoint | Purpose | When to use |
|---|---|---|
| `GET /health/live` | Process is up | Liveness probe (fast, no I/O) |
| `GET /health/ready` | Backends + MCP registry healthy | Readiness probe (gated traffic) |
| `GET /health/dependencies` | Deep per-dependency report (storage / control / governance) | Operator triage |
| `GET /status` | Boot stages, safe-mode, feature flags, MCP tool count | Dashboard + on-call |
| `GET /mcp/tools`, `POST /mcp/tools/{name}` | REST MCP surface | One-shot HTTP calls; SDK + CLI use these |
| `* /mcp/sse`, `* /mcp/http` | Native MCP-protocol transports (SSE + streamable HTTP) — see [docs/MCP_SERVER](docs/MCP_SERVER.md) | MCP-protocol clients (Claude Desktop, Cursor, Code, Zed) |

`/health/live` returns 200 unconditionally as long as the process is
serving. `/health/ready` returns 503 if any storage backend or the
MCP registry is degraded. `/health/dependencies` returns 503 only
when a **required** check is DOWN; governance checks (audit chain
integrity) flag degradation but never gate traffic.

---

## 6. Rolling updates

`docker-compose.production.yml` declares:

```yaml
deploy:
  replicas: 2
  update_config:
    parallelism: 1
    order: start-first
  restart_policy:
    condition: on-failure
    max_attempts: 5
```

`order: start-first` ensures the new replica passes its readiness
probe **before** the old replica is drained — combined with the
boot health gate, this means a broken release never reaches steady
state with zero healthy replicas.

For Kubernetes deployments, replicate this with:

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 1
    maxUnavailable: 0
readinessProbe:
  httpGet: { path: /health/ready, port: 8000 }
  periodSeconds: 5
livenessProbe:
  httpGet: { path: /health/live, port: 8000 }
  periodSeconds: 30
```

---

## 7. Observability wiring

- **Logs** — JSON-structured (structlog), one event per line, with
  `trace_id`, `span_id`, and `request_id` injected on every record.
  Set `LOG_FORMAT=console` only in development.
- **Traces** — OpenTelemetry SDK with OTLP exporter. Set
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4317`. Without an
  endpoint the SDK falls back to `ConsoleSpanExporter` (visible in
  stdout). Span coverage is documented in
  [`docs/15_OBSERVABILITY.md`](docs/15_OBSERVABILITY.md).
- **Metrics** — OTLP metric exporter, 60-second push interval.
  Built-in trackers (`LatencyTracker`, `ThroughputAnalyzer`,
  `SystemHealthMonitor`) emit deterministic rollups.
- **Correlation** — every inbound HTTP request gets an
  `X-Request-ID` (caller-supplied or auto-generated). The id is
  bound to structlog contextvars + the OTEL span and echoed in the
  response. SDK + CLI + UI all forward the header by default.

---

## 8. Scaling knobs

Set in `.env.production`. See [`core/config.py`](core/config.py)
for full descriptions.

| Variable | Default | What it controls |
|---|---|---|
| `SCALE_WORKER_COUNT` | 4 | Worker pool concurrency |
| `SCALE_SHARD_COUNT` | 4 | Graph + vector shard router fan-out |
| `SCALE_RETRIEVAL_CACHE_SIZE` | 1024 | Retrieval cache entries |
| `SCALE_RETRIEVAL_CACHE_TTL_SECONDS` | 300 | Cache TTL (also bounded by version-token invalidation) |
| `SCALE_DEFAULT_RATE_PER_SECOND` | 20 | Per-(caller, resource) rate cap |
| `SCALE_BACKPRESSURE_THRESHOLD` | 0.8 | Queue depth ratio that triggers throttle |

---

## 9. Pre-flight checklist

Before the first production boot:

- [ ] `MCP_API_KEY`, `NEO4J_PASSWORD`, `POSTGRES_PASSWORD` set to
  non-sentinel secrets in your secret store
- [ ] `ENVIRONMENT=production` set in `.env.production`
- [ ] `STRICT_BOOTSTRAP=true`, `LOG_FORMAT=json`, `OTEL_ENABLED=true`
- [ ] `OTEL_EXPORTER_OTLP_ENDPOINT` points at your collector
- [ ] Persistent volumes provisioned for postgres, qdrant, neo4j, redis
- [ ] Backups configured for postgres + neo4j (see RUNBOOK.md §3)
- [ ] Reverse proxy (or LB) configured to forward `X-Forwarded-*` headers
- [ ] `docker compose -f docker-compose.production.yml config` runs clean
- [ ] First boot drained `/health/dependencies` returns `OK` for every
      required check

If any item fails — **don't deploy**. The [RUNBOOK](RUNBOOK.md) covers
the recovery path; this document covers the green path.
