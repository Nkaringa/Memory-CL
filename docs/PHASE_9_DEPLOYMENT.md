# Phase 9 — Deployment Architecture

This document is part of the Phase-9 deliverable set. It defines the
production deployment shape, the deterministic boot sequence, the
service separation map, and the production-readiness checklist.

It does **not** introduce new logic — every component referenced
here was built in Phases 1–8.

---

## 1. Module layout (Phase-9 additive)

```
apps/
  api/
    bootstrap.py             # 8-stage BootSequence
    routers/
      snapshot.py            # POST /snapshot/{build,replay}
      audit.py               # GET  /audit/{tail,verify}
      status.py              # GET  /status
  cli/
    main.py                  # `memcl` console script
  ui/
    static/
      index.html             # 5-tab inspector
      app.css
      app.js                 # vanilla JS — calls existing API only

core/
  safety/
    health_gate.py           # BootStage / HealthGate
    safe_mode.py             # SafeModeController
    feature_flags.py         # FeatureFlagRegistry

sdk/
  client.py                  # AsyncMemoryClient
  types.py                   # Pydantic result models

scripts/
  boot.sh                    # container preflight + exec

Dockerfile.production        # multi-stage non-root prod image
docker-compose.production.yml
.env.{development,staging,production}
```

---

## 2. Service separation map

```
                        ┌──────────────┐
                  ┌────►│   API svc    │◄────┐
                  │     │ (FastAPI)    │     │
                  │     └──────┬───────┘     │
                  │            │             │
   ┌────────────┐ │     ┌──────▼───────┐    │ ┌────────────┐
   │ Worker svc │ │     │ Retrieval svc│    │ │  CLI / SDK │
   │  (ingest   │ │     │ (composes    │    │ │  external  │
   │   queue)   │ │     │  Phase 4-7)  │    │ │  agents    │
   └─────┬──────┘ │     └──────┬───────┘    │ └─────┬──────┘
         │        │            │             │       │
   ┌─────▼────────▼─────┐  ┌───▼────┐   ┌────▼────┐  │
   │   Postgres shard   │  │ Neo4j  │   │ Qdrant  │◄─┘
   │   (canonical)      │  │ (graph)│   │ (vector)│
   └────────────────────┘  └────────┘   └─────────┘
                                  ▲
                                  │
                              ┌───┴────┐
                              │ Redis  │  (cache / lifecycle / quarantine)
                              └────────┘
```

| Service       | Phase | Stateful? | Scaling axis |
|---------------|-------|-----------|--------------|
| API           | 1+5+9 | No        | replicate behind a TCP LB |
| Retrieval     | 4+7   | No        | per-shard worker fan-out |
| Worker        | 2+6   | No        | per-repo queue partitioning |
| Postgres      | 1     | Yes       | read replicas; primary write |
| Neo4j         | 2     | Yes       | per-tenant shard, see Phase 7 |
| Qdrant        | 2/3   | Yes       | per-repo collection, sharded |
| Redis         | 1     | Yes (eph) | sharded cluster |

**Stateless vs. stateful:** All Phase-9 services (`api`, `worker`,
`retrieval`) are stateless and replicate freely. State lives only in
the four backends, which carry their own persistence + replication.

---

## 3. Deterministic boot sequence

Implemented by `apps.api.bootstrap.BootSequence` (run inside
`lifespan`). Each stage maps to a `BootStage` in
`core.safety.health_gate.HealthGate`:

| Order | Stage                      | Probe                                                          |
|-------|----------------------------|----------------------------------------------------------------|
| 1     | `storage_init`             | every Phase-1 client `.ping().ok`                              |
| 2     | `schema_validation`        | Phase-8 `SchemaValidator` smoke OK                             |
| 3     | `graph_vector_validation`  | shard routers route + `graph_repo.neighbors` exists            |
| 4     | `ingestion_readiness`      | Phase-2 `units_repo` exposes upsert/list/delete                |
| 5     | `retrieval_warmup`         | `RankingModel` + `QueryPlanner` constructible                  |
| 6     | `mcp_registry`             | default registry exposes the 14 tools                  |
| 7     | `audit_chain`              | empty audit chain `verify_chain()` returns True                |
| 8     | `api_exposure` (optional)  | `app.state.app_state` populated                                |

The outcome is exposed at `GET /status`. Under `STRICT_BOOTSTRAP=true`,
any failure flips the `SafeModeController` into degraded read-only
mode rather than crashing.

---

## 4. Failure recovery model

| Failure                        | Behavior                                                     |
|--------------------------------|--------------------------------------------------------------|
| Single backend unreachable     | `/status` reports `degraded`; safe-mode if multiple stages   |
| Audit chain tamper detected    | safe-mode auto-enabled; admin must verify + reset            |
| Boot stage failed (required)   | safe-mode enabled (see `SafeModeController`)                 |
| Boot stage failed (optional)   | recorded in `boot_outcome.degraded_stages`; service stays up |
| Retrieval cache miss storm     | Phase-7 backpressure escalates ingestion → retrieval → MCP   |
| Cross-tenant attempt           | Phase-8 `AccessControl` returns 403; audit_event recorded    |

Safe-mode reads stay open; mutating endpoints (`POST /ingest`,
`POST /mcp/tools/ingest_repository`, `POST /mcp/tools/update_memory`)
are expected to consult `state.safe_mode.status.enabled` and return
503 — that wiring is the operator's hook (left non-prescriptive so
existing tests remain green).

---

## 5. Production readiness checklist

- [x] `Dockerfile.production` multi-stage build, non-root user
- [x] `docker-compose.production.yml` with healthchecks + restart policy
- [x] Three env templates (`.env.development|staging|production`)
- [x] Required-secret guards in compose (`${VAR:?required}`)
- [x] `scripts/boot.sh` preflight + exec
- [x] Deterministic 8-stage boot sequence
- [x] `/status` endpoint with full posture
- [x] `SafeModeController` flag + lifespan auto-trigger
- [x] `FeatureFlagRegistry` exposed via `/status`
- [x] CLI (`memcl`) shipped as console-script entry
- [x] SDK (`AsyncMemoryClient`) typed end-to-end
- [x] Read-only inspection UI at `/ui`
- [x] `MCP_API_KEY` enforced in production env template
- [x] Audit chain verification endpoint
- [x] Snapshot + replay endpoints
- [x] OTEL traces enabled by default in prod
- [x] JSON structured logs by default in prod
- [x] All Phase 1–8 contracts unchanged

---

## 6. Operating runbook (one-page)

```
# bring up production stack
cp .env.production .env
# (set POSTGRES_PASSWORD, NEO4J_PASSWORD, MCP_API_KEY in your secret store)
docker compose -f docker-compose.production.yml up -d

# verify boot
curl -fsS http://localhost:8000/status | jq

# ingest a repo
memcl --base-url http://localhost:8000 \
      --api-key "$MCP_API_KEY" \
      ingest /var/repos/acme --repo-id acme --commit-sha "$(git rev-parse HEAD)"

# query
memcl --api-key "$MCP_API_KEY" query "auth flow" --repo-id acme

# audit chain integrity check
curl -fsS -H "X-API-Key: $MCP_API_KEY" http://localhost:8000/audit/verify
```
