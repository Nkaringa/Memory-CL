# Lite Mode (Option B — same app, embedded backends)

**Date:** 2026-06-14 · **Branch:** feat/lite-mode
**Approved:** Option B — run the SAME FastAPI app on localhost wired to embedded
SQLite/numpy/python backends instead of Postgres/Neo4j/Qdrant/Redis. No Docker,
`pip install memory-cl` + `memcl serve`. Default mode stays `server` (live VM
untouched). Verified locally in a clean venv (lite needs no VM).

## Goal
Let an indie dev run Memory-CL on a laptop in under a minute: no Docker, no 4
databases, one pip install. Same parsing / graph / ranking / retrieval / MCP
tools — only the storage engine room changes (the Protocol seam makes this
possible). Identical results; the one tradeoff is local-embedding quality
(already mitigated: drop in an OpenAI key → identical).

## Enabling fact
Every store sits behind a Protocol (`storage/repositories.py`:
IngestionUnitRepository [8], GraphRepository [8], VectorRepository [5]) +
the retrieval-side `VectorSearchClient` / `GraphTraversalSource` Protocols +
the `Embedder` Protocol. ~90% of code consumes the Protocols. Lite = embedded
implementations + a boot switch in `_build_state`. Server code path UNCHANGED.

## Mode switch
`Settings.mode: Literal["server","lite"] = "server"` (env `MEMCL_MODE`).
`_build_state` branches on it. Lite data dir: `Settings.lite_data_dir`
(default `~/.memcl`), holding `data.db` (SQLite) + the fastembed model cache.

## Backend replacements (all in-process)
| Server | Lite |
|---|---|
| Postgres (ingestion_units, app_config, repo_registry, api_tokens) | **SQLite** via `aiosqlite` + SQLAlchemy async (`sqlite+aiosqlite:///~/.memcl/data.db`). SQLite-flavored SQL — no TIMESTAMPTZ/CTE casts (the B14/B15 class vanishes). |
| Qdrant | vectors in a SQLite table + brute-force **numpy** cosine for search |
| Neo4j | nodes/edges in SQLite + **Python BFS** for neighbors/repo_graph |
| Redis | in-memory dict store (lifecycle status + session memory) |
| OpenAI embedder | **LocalEmbedder** (fastembed, already built) by default; OpenAI key = opt-in upgrade |

## Sub-phases (each: build + real round-trip tests against a temp SQLite/dir)
- **LP1 — SQLite SQLAlchemy tables.** `storage/lite/` SQLite client (async engine
  to a file) + SQLite repos implementing the same Protocols/surfaces as the four
  table-backed repos: ingestion (8 methods), app_config (single row), repo_registry,
  api_token. SQLite SQL: `ON CONFLICT` upsert, `CURRENT_TIMESTAMP`, TEXT timestamps,
  idempotent DDL (table-exists + pragma for add-column). Real tests against a tmp db.
- **LP2 — numpy vector store.** `LiteVectorRepository` (VectorRepository Protocol:
  ensure/recreate_collection, upsert_payload(s), delete_points_for_file) storing
  point_id+vector(blob)+payload(json) in SQLite per collection, AND a
  `LiteVectorSearchClient` satisfying the retrieval `VectorSearchClient` Protocol
  (brute-force numpy cosine top-k, payload filter). Real tests.
- **LP3 — python graph + redis stub.** `LiteGraphRepository` (GraphRepository +
  the `GraphTraversalSource` subset: nodes/edges in SQLite, BFS neighbors/repo_graph,
  edges_among, delete_subgraph_for_file). `InMemoryKeyValue` replacing the narrow
  Redis surface the lifecycle (decay/state_scanner) + memory_tool use — or scope
  those off in lite. Real tests.
- **LP4 — boot wiring + seams.** `_build_state` lite branch assembles the embedded
  backends. Resolve the two raw-client couplings the scan flagged: MCP tools' raw
  `state.postgres.engine` (lite = a SQLite SQLAlchemy engine — verify the tools'
  SQL is SQLite-safe; fix any Postgres-ism) and `state.qdrant.client` →
  `LiteVectorSearchClient`. Default `LocalEmbedder`. End-to-end test: boot the app
  (TestClient) in lite mode in a tmp dir, ingest a tiny fixture repo, search/read —
  all green, zero external services.
- **LP5 — package + ship.** pyproject: `aiosqlite` dep + a `memcl serve` console
  entry (uvicorn, lite mode, opens localhost) + `memcl ui`. README quickstart
  (`pip install` → `memcl serve` → `memcl ingest .`). Clean-venv verification.
  Full server-mode suite stays green throughout. Merge.

## Limits (= indie's non-needs; documented, with a migration path)
Single-user / single-writer (SQLite lock), comfortable to ~100k units, no
multi-tenant. Beyond → server tier via `memcl export` → server → `import`
(same data model, no lock-in). Server-mode behavior is never changed by any of this.

## Non-negotiables
- `MEMCL_MODE` defaults to `server`; the live VM + all existing tests behave
  EXACTLY as before. Lite is purely additive (new `storage/lite/` package +
  one `_build_state` branch).
- Every lite backend implements the SAME Protocol the server one does — pipelines,
  ranking, MCP tools stay byte-identical in behavior (modulo embedding model).
