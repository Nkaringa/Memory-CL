# 15 · Observability

← back to [index](00_INDEX.md) · related: [24_TROUBLESHOOTING](24_TROUBLESHOOTING.md), [23_PERFORMANCE_AND_SCALING](23_PERFORMANCE_AND_SCALING.md), [14_DISTRIBUTED_SYSTEM](14_DISTRIBUTED_SYSTEM.md)

Three signal types: **traces** (OpenTelemetry), **structured logs**
(structlog), and **rolling metrics** (Phase 7 trackers).

## OpenTelemetry traces

Bootstrapped in `core/observability/_otel.py`. Exporters:

- `OTEL_EXPORTER_OTLP_ENDPOINT` set → OTLP/gRPC
- otherwise → console exporter (so spans never silently drop in dev)

Span coverage by phase:

| Phase | Span name | Where |
|---|---|---|
| 2 | `python_parser.parse_file` | `core/parsing/python_parser.py` |
| 2 | `graph_builder.build` | `core/ingestion/graph_builder.py` |
| 2 | `ingestion.run`, `ingestion.ingest_file` | `core/ingestion/pipeline.py` |
| 2 | `postgres_repo.{ensure_schema,upsert_unit,upsert_units,delete_units_for_file}` | `storage/postgres_repo.py` |
| 2 | `neo4j_repo.{ensure_constraints,upsert_node,upsert_edge,delete_subgraph_for_file}` | `storage/neo4j_repo.py` |
| 2 | `qdrant_repo.{ensure_collection,upsert_payloads,delete_points_for_file}` | `storage/qdrant_repo.py` |
| 3 | `dense_encoder.encode_unit` | `core/compression/dense_encoder.py` |
| 3 | `module_summarizer.summarize`, `api_summarizer.summarize`, `graph_summarizer.summarize` | `core/summarization/` |
| 3 | `chunking.chunk_unit`, `embedding_pipeline.run` | `core/embeddings/` |
| 3 | `compression.run` | `core/compression/pipeline.py` |
| 4 | `query_planner.run`, `graph_retriever.search`, `vector_retriever.search`, `metadata_retriever.query` | `core/retrieval/` |
| 4 | `ranking_engine.score` | `core/ranking/ranking_model.py` |
| 4 | `context_assembler.build` | `core/context/context_assembler.py` |
| 4 | `hybrid_retriever.run` | `core/retrieval/hybrid_retriever.py` |
| 5 | `mcp.tool.execution`, `mcp.tool.validation` | `core/mcp/execution/tool_executor.py` |
| 5 | `mcp.server.request` | `apps/mcp/router.py` |
| 6 | `relevance_scorer.compute`, `decay_engine.run`, `memory_compactor.plan`, `graph_compactor.merge`, `embedding_refresh.trigger`, `lifecycle.scan` | `core/lifecycle/` |
| 8 | (governance / integrity / replay events go through audit + spans on each module) | `core/governance/`, `core/integrity/`, `core/reproducibility/` |

Common attributes: `repo_id`, `query_id` (retrieve), `request_id` (MCP),
`unit_id`, `commit_sha`, `tenant_id`.

## Structured logs

`core/logging.py` configures structlog:

- JSON renderer in production (`LOG_FORMAT=json`)
- console renderer in dev
- automatic OTEL trace_id/span_id injection
- stdlib bridge so `uvicorn.access` / `httpx` etc. flow through

Per-phase log helpers enforce the spec'd event shape:

| Phase | Helper | Event signature |
|---|---|---|
| 2 | `core.ingestion.logevent.emit_phase2_event` | `event, phase=phase_2, operation, status, duration_ms, unit_id, file_path, content_hash` |
| 3 | `core.compression.logevent.emit_phase3_event` | `event, phase=phase_3, operation, status, duration_ms, unit_id, token_reduction_ratio` |
| 4 | `core.retrieval.logevent.emit_phase4_event` | `event, phase=phase_4, operation, status, latency_ms, query_id, repo_id` (+ channel hits) |
| 5 | `core.mcp.logevent.emit_mcp_event` | `event=mcp_tool_call, phase=phase_5, tool, request_id, status, latency_ms, user_scope` |
| 6 | `core.lifecycle.logevent.emit_phase6_event` | `event=memory_evolution, phase=phase_6, entity_id, operation, relevance_score, status` |
| 7 | `core.observability.logevent.emit_phase7_event` | `event=system_scale_event, phase=phase_7, metric, latency_ms, throughput, shard_id, status` |
| 8 | (audit chain entries — see [16](16_AUDIT_AND_GOVERNANCE.md)) | |

Spec keys are **enforced by keyword-only signatures** so callers
cannot accidentally drop a required field.

## Rolling metrics (Phase 7)

`core/observability/latency_tracker.py::LatencyTracker`:

- per-`(metric, shard_id)` rolling window
- p50 / p95 / p99 / mean / max via linear-interpolated percentiles
- bounded memory (default `window_size=256`)
- pure deterministic — sort + index, no PRNG

`core/observability/throughput_analyzer.py::ThroughputAnalyzer`:

- per-`(metric, shard_id)` rolling event counter
- lazy eviction on record + snapshot (uses a deque)
- time threaded in via `now`

`core/observability/system_health_monitor.py::SystemHealthMonitor`:

- aggregates backend probes + latency thresholds → `HealthSnapshot`
- `OK` / `DEGRADED` / `FAILED`
- `OK` requires every probe healthy + every metric below `p99_latency_ms` threshold

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health/live` | Liveness — process up |
| `GET /health/ready` | Readiness — every backend pingable |
| `GET /status` | Full posture: env + safe_mode + flags + boot stages + MCP count |
| `GET /audit/tail?limit=N` | Recent audit chain entries |
| `GET /audit/verify` | Re-walk the chain |

## Observability discipline

When adding a new module:

1. Open an OTEL span in every public async method.
2. Set span attributes for the IDs that thread through (repo_id,
   request_id, unit_id, etc.).
3. Use the per-phase `emit_phaseN_event` helper for state
   transitions (start, end, failure).
4. Never raise out of an audit/log path — wrap in try/except and
   degrade to warn-level.

---

Next: [16 — Audit + Governance](16_AUDIT_AND_GOVERNANCE.md)
