# Memory-CL · Documentation Index

> A navigable operating-system manual for a deterministic AI memory engine.

Memory-CL transforms a codebase into a queryable knowledge surface
(graph + vectors + canonical store) and exposes it through HTTP, MCP
tools, an SDK, a CLI, and a transparency UI. Same input + same state
→ same output, every time. Phases 1–9 implement the engine; Phase 10
adds the cognitive interface.

---

## Start here

| You are… | Read in this order |
|---|---|
| **A new developer** | [01_OVERVIEW](01_OVERVIEW.md) → [02_ARCHITECTURE](02_ARCHITECTURE.md) → [04_INSTALLATION](04_INSTALLATION.md) → [05_LOCAL_DEVELOPMENT](05_LOCAL_DEVELOPMENT.md) → [03_DATA_FLOW](03_DATA_FLOW.md) |
| **Operating in production** | [21_DEPLOYMENT](21_DEPLOYMENT.md) → [06_CONFIGURATION](06_CONFIGURATION.md) → [22_SECURITY_AND_ACCESS_CONTROL](22_SECURITY_AND_ACCESS_CONTROL.md) → [15_OBSERVABILITY](15_OBSERVABILITY.md) → [24_TROUBLESHOOTING](24_TROUBLESHOOTING.md) |
| **Contributing code** | [02_ARCHITECTURE](02_ARCHITECTURE.md) → [25_DESIGN_DECISIONS](25_DESIGN_DECISIONS.md) → [05_LOCAL_DEVELOPMENT](05_LOCAL_DEVELOPMENT.md) → relevant module doc |
| **Integrating an agent** | [07_API_REFERENCE](07_API_REFERENCE.md) → [08_MCP_TOOLING](08_MCP_TOOLING.md) → [20_SDK_GUIDE](20_SDK_GUIDE.md) |
| **Debugging a query** | [03_DATA_FLOW](03_DATA_FLOW.md) → [09_RETRIEVAL_SYSTEM](09_RETRIEVAL_SYSTEM.md) → [10_RANKING_ENGINE](10_RANKING_ENGINE.md) → [18_UI_GUIDE](18_UI_GUIDE.md) |

Quick links: [Glossary](26_GLOSSARY.md) · [API reference](07_API_REFERENCE.md) · [CLI reference](19_CLI_REFERENCE.md)

---

## Full index

### Foundation
- [01 — Overview](01_OVERVIEW.md) · what the system is, mental model, invariants
- [27 — Feature Matrix](27_FEATURE_MATRIX.md) · every feature × audience tier, with verified maturity labels
- [02 — Architecture](02_ARCHITECTURE.md) · layers, dependency rules, phase mapping
- [03 — Data Flow](03_DATA_FLOW.md) · ingest / retrieve / MCP request lifecycles
- [25 — Design Decisions](25_DESIGN_DECISIONS.md) · why this shape

### Setup + workflow
- [04 — Installation](04_INSTALLATION.md) · prerequisites + first boot
- [05 — Local Development](05_LOCAL_DEVELOPMENT.md) · dev loop + testing
- [06 — Configuration](06_CONFIGURATION.md) · env vars, settings, feature flags

### Reference
- [07 — API Reference](07_API_REFERENCE.md) · every HTTP endpoint
- [19 — CLI Reference](19_CLI_REFERENCE.md) · `memcl` commands
- [20 — SDK Guide](20_SDK_GUIDE.md) · `AsyncMemoryClient` (Python + TS)
- [26 — Glossary](26_GLOSSARY.md) · core terms

### Engine modules
- [08 — MCP Tooling](08_MCP_TOOLING.md) · the 14 agent tools (v2 surface)
- [MCP Server](MCP_SERVER.md) · native MCP-protocol server (HTTP/SSE)
- [MCP Bridge](MCP_BRIDGE.md) · stdio bridge for stdio-only MCP clients
- [09 — Retrieval System](09_RETRIEVAL_SYSTEM.md) · vector + graph + metadata + hybrid
- [10 — Ranking Engine](10_RANKING_ENGINE.md) · `0.35/0.25/0.20/0.15/0.05` formula
- [11 — Graph System](11_GRAPH_SYSTEM.md) · BFS, EXTERNAL handling, edges
- [12 — Embeddings + Compression](12_EMBEDDINGS_AND_COMPRESSION.md) · dense schema, chunking
- [13 — Memory Evolution](13_MEMORY_EVOLUTION.md) · decay, refresh, compaction

### Infrastructure
- [14 — Distributed System](14_DISTRIBUTED_SYSTEM.md) · sharding, workers, backpressure
- [15 — Observability](15_OBSERVABILITY.md) · OTEL, latency, throughput, health
- [23 — Performance + Scaling](23_PERFORMANCE_AND_SCALING.md) · cache, batching, bottlenecks

### Governance
- [16 — Audit + Governance](16_AUDIT_AND_GOVERNANCE.md) · hash chain, tenant, policy
- [17 — Snapshot + Replay](17_SNAPSHOT_AND_REPLAY.md) · deterministic state capture
- [22 — Security + Access Control](22_SECURITY_AND_ACCESS_CONTROL.md) · auth, isolation

### Surfaces
- [18 — UI Guide](18_UI_GUIDE.md) · Next.js transparency layer

### Operations
- [21 — Deployment](21_DEPLOYMENT.md) · Docker, compose, scaling
- [24 — Troubleshooting](24_TROUBLESHOOTING.md) · common failures + recovery

---

## Phase map

| Phase | Surface area | Doc anchor |
|---|---|---|
| 1 | FastAPI skeleton, storage clients, config, OTEL bootstrap, health | [02](02_ARCHITECTURE.md) [04](04_INSTALLATION.md) |
| 2 | AST extraction, graph builder, ingestion pipeline, three storage repos | [03](03_DATA_FLOW.md) [11](11_GRAPH_SYSTEM.md) |
| 3 | Dense compression, summarization, chunking, embedding pipeline | [12](12_EMBEDDINGS_AND_COMPRESSION.md) |
| 4 | Hybrid retrieval, ranking model, context assembly | [09](09_RETRIEVAL_SYSTEM.md) [10](10_RANKING_ENGINE.md) |
| 5 | MCP tools, executor, registry, auth | [08](08_MCP_TOOLING.md) |
| 6 | Lifecycle: relevance, decay, compaction, refresh | [13](13_MEMORY_EVOLUTION.md) |
| 7 | Sharding, worker pool, backpressure, rate limiting, batching | [14](14_DISTRIBUTED_SYSTEM.md) [23](23_PERFORMANCE_AND_SCALING.md) |
| 8 | Audit chain, integrity, governance, snapshot, replay, drift, diagnostics | [16](16_AUDIT_AND_GOVERNANCE.md) [17](17_SNAPSHOT_AND_REPLAY.md) |
| 9 | Boot orchestration, safe-mode, CLI, SDK, production packaging | [21](21_DEPLOYMENT.md) [19](19_CLI_REFERENCE.md) [20](20_SDK_GUIDE.md) |
| 10 | Next.js transparency UI | [18](18_UI_GUIDE.md) |

---

## House rules

1. **Determinism first.** Same input + same state → byte-identical output across runs.
2. **No randomness in core paths.** Time / IDs / vectors are passed in or derived from content hashes.
3. **Audit everything.** Every governance + tool action emits a hash-chained `audit_event`.
4. **Never delete data.** Decay downgrades; quarantine flags; compaction proposes plans. Hard delete is out of scope.
5. **Schemas are versioned and immutable.** Bumping `SCHEMA_VERSION` requires a migration.

If a doc here disagrees with the code, the code wins — but please open a PR fixing the doc.
