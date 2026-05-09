# 01 · Overview

← back to [index](00_INDEX.md)

## What this is

Memory-CL is a **deterministic AI memory engine**. It ingests a
codebase, projects it onto three storage backends (Postgres for
canonical data, Neo4j for the graph, Qdrant for vectors), and exposes
a hybrid retrieval surface that an agent can query without ever
reading the full repository.

The engine is wrapped by:

- a thin HTTP API (FastAPI),
- seven MCP tools (the agent surface),
- a Python SDK + `memcl` CLI (the developer surface),
- a Next.js transparency UI (the cognitive surface).

## Why it exists

Agents that read whole codebases waste tokens, hit rate limits, and
have no way to explain why an answer surfaced. Memory-CL pre-builds a
queryable representation, ranks results with a fixed formula, and
returns a context packet whose every entry can be traced back to the
modules that produced it.

## Mental model

```
                       ┌──────────────────────┐
                       │   query / agent task │
                       └──────────┬───────────┘
                                  │
                       ┌──────────▼──────────┐
                       │ Phase-4 retrieval   │  hybrid: vector ⊕ graph ⊕ metadata
                       └──────────┬──────────┘
                                  │ candidates
                       ┌──────────▼──────────┐
                       │ Phase-4 ranking     │  0.35 · sem + 0.25 · graph + 0.20 · rec
                       │   (mandated)        │  + 0.15 · imp + 0.05 · feedback
                       └──────────┬──────────┘
                                  │ ranked
                       ┌──────────▼──────────┐
                       │ Phase-4 assembly    │  priority order:
                       │                     │  constraints > risks > arch > logic > code
                       └──────────┬──────────┘
                                  │
                       ┌──────────▼──────────┐
                       │   ContextPacket     │  ← what the agent receives
                       └─────────────────────┘
```

Surfaces sit on top of this:

```
HTTP /retrieve  ────►  Phase-4 ranked + assembled
HTTP /mcp/tools ────►  Phase-5 executor → Phase-2/3/4 systems
SDK get_context ────►  same as above, typed
CLI memcl query ────►  same as above, JSON to stdout
UI  /retrieve   ────►  same as above + per-entry "Why this result?"
```

## Key invariants

1. **Determinism.** No PRNG, no clock-derived branching in the
   intelligence path. `now` is threaded as data when timing matters.
   Sorted iteration, sorted output, content-hashed IDs.
2. **Cross-store identity.** A unit's `unit_id` is the SAME string in
   Postgres, Neo4j, and Qdrant. No translation tables.
3. **Append-only audit.** Every governance / MCP / tool decision emits
   a hash-chained `audit_event`. Tampering breaks the chain.
4. **Never delete.** Decay downgrades to `low_priority_index`. Drift
   schedules refresh. Quarantine flips a Redis flag. Hard delete is
   out of scope until Phase 11+.
5. **Phases are immutable past their gate.** Each phase ends with a
   green test gate; later phases do not modify earlier code.

## Layered system diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ apps/  (entry points)                                          │
│  ├── api      FastAPI service (Phases 1, 5, 9 routers)         │
│  ├── mcp      MCP server + auth + registry                     │
│  ├── cli      memcl console script                              │
│  └── ui       Phase-9 static inspector + Phase-10 Next.js UI    │
├─────────────────────────────────────────────────────────────────┤
│ core/  (intelligence)                                          │
│  ├── parsing      AST → IngestionUnit                          │
│  ├── ingestion    pipeline orchestrator + graph builder        │
│  ├── compression  dense encoder + serializer + module compactor│
│  ├── summarization  module/api/graph summarizers               │
│  ├── embeddings   chunker + embedder + embedding pipeline      │
│  ├── retrieval    graph + vector + metadata + hybrid + planner │
│  ├── ranking      mandated formula + scoring + tie-break       │
│  ├── context      assembler + budget optimizer                 │
│  ├── mcp          executor + registry + 7 tools                │
│  ├── lifecycle    decay + compaction + refresh + scoring       │
│  ├── analytics    usage + feedback + performance               │
│  ├── scaling      shard routers + retrieval cache              │
│  ├── performance  rate limiter + backpressure + batching       │
│  ├── observability  OTEL + latency + throughput + health       │
│  ├── governance   audit logger + tenants + policy + AC         │
│  ├── integrity    checksum + graph validator + drift           │
│  ├── reproducibility  state versioning + snapshot + replay     │
│  ├── diagnostics  anomaly + corruption + consistency           │
│  └── safety       boot health gate + safe mode + flags         │
├─────────────────────────────────────────────────────────────────┤
│ storage/  (adapters)                                           │
│  ├── postgres / neo4j / qdrant / redis  (Phase 1 clients)      │
│  ├── postgres_repo / neo4j_repo / qdrant_repo                  │
│  └── repositories  (Protocol contracts)                        │
├─────────────────────────────────────────────────────────────────┤
│ schemas/  (contracts)                                          │
│  ingest · graph · dense · compression · retrieval · health     │
├─────────────────────────────────────────────────────────────────┤
│ infra/  (cluster primitives)                                   │
│  ├── distributed   worker pool + scheduler + load balancer     │
│  └── audit         immutable log store + sinks                 │
└─────────────────────────────────────────────────────────────────┘
```

## What this overview is NOT

- A request walkthrough → see [03_DATA_FLOW](03_DATA_FLOW.md)
- A module reference → see the per-module docs (`08`–`17`)
- An installer → see [04_INSTALLATION](04_INSTALLATION.md)

---

Next: [02 — Architecture](02_ARCHITECTURE.md)
