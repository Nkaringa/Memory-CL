# Architecture

Layered architecture overview of Memory-CL — what the running system
looks like, how it's organized in the source tree, and the contracts
that hold the layers together.

## Cross-references

- **[DEPLOYMENT.md](DEPLOYMENT.md)** — how to deploy the artifact
- **[RUNBOOK.md](RUNBOOK.md)** — how to recover from incidents
- **[SECURITY.md](SECURITY.md)** — threat model + access controls
- **[docs/02_ARCHITECTURE.md](docs/02_ARCHITECTURE.md)** — layered
  architecture spec (deeper detail)
- **[docs/00_INDEX.md](docs/00_INDEX.md)** — full doc index

---

## 1. Components at runtime

```
                        ┌─────────────────────────────┐
                        │         UI (Next.js)        │
                        │  ui/Dockerfile.production   │
                        └──────────────┬──────────────┘
                                       │ /api/* rewrites
                                       ▼
  ┌──────────┐    ┌────────────────────────────────────┐    ┌───────────┐
  │  CLI     │───▶│             API (FastAPI)          │◀───│   SDK     │
  │ memcl    │    │  apps/api  +  apps/mcp  +  worker  │    │ AsyncMC   │
  └──────────┘    │      Dockerfile.production         │    └───────────┘
                  └─┬────────────┬──────────┬──────────┘
                    │            │          │
                    ▼            ▼          ▼
            ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
            │ Postgres │  │  Neo4j   │  │ Qdrant   │  │  Redis   │
            │ ingest + │  │ graph    │  │ vector   │  │ cache +  │
            │ metadata │  │          │  │          │  │ version  │
            └──────────┘  └──────────┘  └──────────┘  └──────────┘
                    ▲
                    │ append-only
            ┌───────┴─────────┐
            │ Audit JSONL     │
            │ (durable sink)  │
            └─────────────────┘
```

All clients (UI, SDK, CLI) talk to the API over HTTP. There is no
direct backend access — the API owns every read and write to
Postgres, Neo4j, Qdrant, Redis, and the audit JSONL sink.

---

## 2. Source layout

| Path | Layer | What lives here |
|---|---|---|
| `apps/api/` | HTTP surface | FastAPI app, routers, lifespan, middleware |
| `apps/mcp/` | Agent surface | MCP tool registry + executor + native MCP server (SSE/HTTP) + ASGI auth |
| `apps/cli/` | Operator surface | `memcl` CLI |
| `sdk/` | Python client | `AsyncMemoryClient`, types |
| `core/` | Business logic | retrieval, ranking, ingestion, governance, scaling, observability, safety, config, logging |
| `storage/` | Backend clients | Postgres, Qdrant, Neo4j, Redis client wrappers |
| `schemas/` | Wire schemas | Pydantic shapes shared by API, SDK, CLI |
| `infra/` | Cross-cutting infra helpers | (e.g. shared bootstrap utilities) |
| `scripts/` | Boot orchestration | `boot.sh` |
| `ui/` | Next.js UI | Pages, components, primitives |
| `docs/` | Documentation | Module-by-module narratives |
| `tests/` | Test suite | Unit + integration |

---

## 3. The dependency rule

```
schemas  ←  storage  ←  core  ←  apps  ←  sdk
                    \           /
                     \─ infra ─/
```

- **`schemas/` depends on nothing** — wire shapes only
- **`storage/` depends on `schemas/`** — no business logic
- **`core/` depends on `schemas/`, `storage/`, `infra/`** — every
  business concern lives here, never reaches into HTTP
- **`apps/` depend on everything below** — HTTP / CLI / agent surfaces
  are thin orchestration over `core`
- **`sdk/` depends only on `schemas/`** — clients never import core

This rule is enforced by import discipline. Violating it surfaces in
review; CI gating is a future tightening.

---

## 4. Boot sequence

`scripts/boot.sh` (container ENTRYPOINT) runs minimal pre-flight
checks, then `exec`s into uvicorn. Inside the running process,
[`apps/api/lifespan.py`](apps/api/lifespan.py) wires the long-lived
state and runs the deterministic 8-stage health gate:

```
1. storage_init            — storage clients connect
2. schema_validation       — SchemaValidator smoke check
3. graph_vector_validation — Neo4j constraints + Qdrant routability
4. ingestion_readiness     — ingestion_units table reachable
5. retrieval_warmup        — RankingModel + retrievers constructible
6. mcp_registry            — registry exposes ≥7 tools
7. audit_chain             — ImmutableLogStore reachable + verifies clean
8. api_exposure            — last; non-required
```

Outcomes are surfaced at `/status`. Failed required stages flip the
process into safe-mode (`read_only` by default).

---

## 5. Request lifecycle

A `POST /retrieve` from the UI demonstrates the full path:

```
UI fetch  ──┐
            │ X-Request-ID: <uuid>
            ▼
  ┌─────────────────────────────────────────────────┐
  │ apps/api/middleware.py::RequestContextMiddleware │  ← bind request_id to logs + OTEL
  └─────────────────────────────────────────────────┘
            │
            ▼
  ┌────────────────────────────────────┐
  │ apps/api/routers/retrieve.py       │  ← thin orchestration
  └────────────────────────────────────┘
            │
            ▼
  ┌────────────────────────────────────┐
  │ core.retrieval.HybridRetriever     │  ← graph + vector + metadata
  └─────────┬──────────┬──────────┬────┘
            ▼          ▼          ▼
       Neo4jClient  Qdrant   PostgresClient
            │          │          │
            └──────────┴──────────┘
                       │
                       ▼
  ┌────────────────────────────────────┐
  │ core.ranking.RankingModel          │  ← fixed-weight blend
  └────────────────────────────────────┘
            │
            ▼
  ┌────────────────────────────────────┐
  │ core.context.ContextAssembler      │  ← deterministic packing
  └────────────────────────────────────┘
            │
            ▼
   RetrieveResponse → JSON, with X-Request-ID echoed back
```

Every layer above gets the same `request_id` via structlog
contextvars + the OTEL span — ops can reconstruct the full path
across the four storage backends from a single id.

---

## 6. Determinism contract

Three hard invariants:

1. **Same input + same state ⇒ byte-identical output.** Pinned by
   the determinism test suite.
2. **Same operation, replayed against the matching snapshot ⇒
   identical hash.** Pinned by `test_replay_engine_reports_match_for_deterministic_op`.
3. **Tampering with the audit JSONL ⇒ chain breaks at the first
   modified link.** Pinned by `/audit/verify` returning
   `{"intact": false, "broken_at_seq": N}`.

The system never randomizes outputs, never reorders for "performance",
never silently substitutes backends. A request that produced X
yesterday produces X today, given the same state version.

---

## 7. Observability surface

| Signal | Surface | Tool |
|---|---|---|
| Logs | structlog JSON, one line per event | stdout → log aggregator |
| Traces | OTLP spans across ingestion, retrieval, ranking, MCP (REST + native), snapshot, replay | collector → Jaeger / Tempo / etc. |
| MCP transports | REST (`/mcp/tools`), SSE (`/mcp/sse`), streamable HTTP (`/mcp/http`) — see [docs/MCP_SERVER](docs/MCP_SERVER.md) | clients pick whichever they speak |
| Metrics | OTLP metrics, 60s push interval | collector → Prometheus / OTel-LGTM |
| Health | `/health/live`, `/health/ready`, `/health/dependencies` | orchestrator probes |
| Posture | `/status` | dashboard + RUNBOOK triage |
| Audit | `/audit/tail`, `/audit/verify` | governance review |
| Determinism | `/snapshot/build`, `/snapshot/replay` | compliance + regression |

Correlation: every request gets an `X-Request-ID` (caller-supplied
or generated). Bound to logs, OTEL spans, and echoed in the response.

---

## 8. Safe-mode as a first-class state

The `SafeModeController` carries an explicit `mode` discriminator:

| `mode` | Mutations | MCP | Retrieve | Health |
|---|---|---|---|---|
| `off` | ✓ | ✓ | ✓ | ✓ |
| `read_only` | 503 | ✓ (read-only tools) | ✓ | ✓ |
| `mcp_disabled` | ✓ | 503 | ✓ | ✓ |
| `retrieval_only` | 503 | 503 | ✓ | ✓ |

Operators choose which mode to engage based on the incident. The
boot health gate auto-engages `read_only` if a required stage fails
under `STRICT_BOOTSTRAP=true`. Manual transitions are atomic and
reflected at `/status`.

---

## 9. Image topology

| Image | Stage 1 | Stage 2 | Final |
|---|---|---|---|
| `memory-cl:prod` | builder (gcc + pip wheel) | runtime (slim, non-root) | tini → boot.sh → uvicorn |
| `memory-cl-ui:prod` | deps (npm ci) → builder (next build) | runtime (node:slim, non-root) | tini → node server.js |

Both are multi-stage, both run as uid 1000, both ship without
compilers in the runtime stage.

---

## 10. What this architecture explicitly is **not**

- **Not eventually consistent** — every read sees the most recent
  committed write. Caching is invalidated by version tokens.
- **Not multi-region by default** — single-region deployments are
  the supported path; multi-region is on the roadmap.
- **Not a vector DB** — Qdrant is the vector store, but it's an
  implementation detail behind `core.retrieval.VectorRetriever`.
- **Not autoscaling** — capacity is configured via env (`SCALE_*`).
  Autoscaling is your orchestrator's job; the API just exposes
  truthful health signals.
