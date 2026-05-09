# 02 · Architecture

← back to [index](00_INDEX.md) · related: [01_OVERVIEW](01_OVERVIEW.md), [25_DESIGN_DECISIONS](25_DESIGN_DECISIONS.md), [03_DATA_FLOW](03_DATA_FLOW.md)

## Layered architecture

Memory-CL follows a strict layered model. Higher layers may depend on
lower layers; the reverse is forbidden.

```
apps/   ─┐
         │  may import any layer below
core/   ─┤  may import storage + schemas + infra
infra/  ─┤  may import schemas only
storage/─┤  may import schemas only
schemas/ ── pure data contracts; no internal imports
```

Forbidden directions:

- `storage/`  →  `apps/`         (never)
- `storage/`  →  `core/`         (never; storage takes config via constructor)
- `schemas/`  →  anything except stdlib + pydantic
- `core/retrieval/`  →  `apps/`  (retrieval must not know which API mounts it)
- `infra/`    →  `apps/`         (infra is composed by apps, not vice-versa)

Verify with:

```bash
grep -rn "from apps" core/ storage/ schemas/ infra/
# (should print nothing)
```

## Phase mapping

Each phase added an additive surface; none rewrote the previous.

| Phase | Adds | Touches |
|---|---|---|
| 1 | FastAPI app, `core/config`, `core/observability` (now package), four storage clients, `/health/{live,ready}` | new files only |
| 2 | `schemas/{ingest,graph,dense}`, `core/parsing/`, `core/ingestion/`, `storage/{postgres,neo4j,qdrant}_repo`, `/ingest` | new only |
| 3 | `schemas/compression`, `core/compression/`, `core/summarization/`, `core/embeddings/` | new only |
| 4 | `schemas/retrieval`, `core/retrieval/`, `core/ranking/`, `core/context/`, `/retrieve` | new only |
| 5 | `core/mcp/`, `apps/mcp/`, `/mcp/tools{,/X}` | new only + lifespan extension |
| 6 | `core/lifecycle/`, `core/analytics/` | new only |
| 7 | `core/scaling/`, `core/performance/`, `infra/distributed/`, `core/observability/{latency,throughput,health}` | observability becomes package |
| 8 | `core/governance/`, `core/integrity/`, `core/reproducibility/`, `core/diagnostics/`, `infra/audit/` | new only |
| 9 | `core/safety/`, `apps/api/{bootstrap,routers/{snapshot,audit,status}}`, `apps/cli/`, `apps/ui/static/`, `sdk/`, prod packaging | new only + lifespan extension + main router mount |
| 10 | `ui/` (Next.js) | brand-new top-level project |

## Module responsibility map

```
apps/api/           ──── HTTP entry (lifespan, routers)
apps/mcp/           ──── MCP server (auth, registry, router)
apps/cli/           ──── memcl console script
apps/ui/static/     ──── Phase-9 read-only HTML inspector

core/parsing/        ──► AST → IngestionUnit list
core/ingestion/      ──► orchestrate parse + build + write to 3 stores
core/compression/    ──► IngestionUnit → DenseRecord; deterministic JSON
core/summarization/  ──► DenseModule / DenseApi / DenseGraphSlice
core/embeddings/     ──► chunk + embed + write payloads
core/retrieval/      ──► graph + vector + metadata channels + hybrid
core/ranking/        ──► mandated formula + tie-break
core/context/        ──► priority-ordered assembler + budget optimizer
core/mcp/            ──► tool executor + 7 tools + validator
core/lifecycle/      ──► decay + compaction + refresh planners
core/analytics/      ──► usage + feedback + performance signals
core/scaling/        ──► shard routers + retrieval cache + distributor
core/performance/    ──► rate limit + backpressure + batching
core/observability/  ──► OTEL + latency + throughput + health monitor
core/governance/     ──► audit + tenants + policy + access control
core/integrity/      ──► checksum + graph + schema + drift
core/reproducibility/──► state version + snapshot + replay engine
core/diagnostics/    ──► anomaly + corruption + consistency reports
core/safety/         ──► boot health gate + safe-mode + feature flags

storage/postgres_repo  ──► IngestionUnit canonical store
storage/neo4j_repo     ──► graph nodes + edges
storage/qdrant_repo    ──► vector payloads + (Phase 3+) vectors
storage/repositories   ──► Protocol contracts (the public storage surface)

infra/distributed/  ──► WorkerPool, TaskScheduler, ShardManager, LoadBalancer
infra/audit/        ──► ImmutableLogStore + sinks
```

## Architecture rules (enforced)

1. **Determinism rule** — every module that produces output must
   produce identical output for identical input + state. See
   [10_RANKING_ENGINE](10_RANKING_ENGINE.md) and the determinism tests
   under `tests/test_golden_phase*.py`.
2. **`unit_id ≡ node_id ≡ point_id`** — Postgres / Neo4j / Qdrant
   share the same primary key per unit. Pinned by
   `tests/test_phase1_compatibility.py`.
3. **`EDGE_RULES` are mandatory** — every graph edge passes through
   `is_edge_allowed()` before write. See [11_GRAPH_SYSTEM](11_GRAPH_SYSTEM.md).
4. **Mandated ranking weights** — `0.35 / 0.25 / 0.20 / 0.15 / 0.05`
   sum to 1.0; `FeatureWeights` rejects anything else.
5. **Audit chain is the only mutable governance state.** Even policy
   decisions emit an event before they take effect.

## Cross-cutting concerns

- **Observability** — every Phase-2..8 path opens an OTEL span and
  emits a structured `phase_N` log. See [15_OBSERVABILITY](15_OBSERVABILITY.md).
- **Failure isolation** — per-file ingestion errors, per-channel
  retrieval errors, per-shard worker errors all degrade gracefully.
  Boot stages map to the same model (`required` vs not).
- **Schemas** — every persisted contract derives from `VersionedModel`
  and carries `schema_version + created_at + updated_at + checksum`.

---

Next: [03 — Data Flow](03_DATA_FLOW.md)
