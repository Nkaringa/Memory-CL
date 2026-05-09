# 03 · Data Flow

← back to [index](00_INDEX.md) · related: [02_ARCHITECTURE](02_ARCHITECTURE.md), [09_RETRIEVAL_SYSTEM](09_RETRIEVAL_SYSTEM.md), [08_MCP_TOOLING](08_MCP_TOOLING.md)

Three lifecycles to understand: **ingest**, **retrieve**, **MCP tool**.
Each is described as a sequence of pure stages so you can drop a
breakpoint at any boundary.

---

## Ingest

Triggered by `POST /ingest` or the `ingest_repository` MCP tool.

```
client
  │  POST /ingest { repo_id, repo_path, commit_sha }
  ▼
apps/api/routers/ingest.py
  │  validate request (Pydantic)
  │  ensure_collection on Qdrant for this repo
  ▼
core/ingestion/pipeline.py · IngestionPipeline.run(ctx)
  │
  │ ── pass 1: parse all files ─────────────────────────────────
  │  core/parsing/file_walker.py · FileWalker.walk()
  │    → sorted FileRef list (deterministic POSIX path order)
  │  for each file:
  │    core/parsing/python_parser.py · PythonParser.parse_file()
  │      → list[IngestionUnit]  (module + classes + fns + consts)
  │    failures: log + skip; reported in `failed_files`
  │
  │ ── build cross-file qname resolver ──────────────────────────
  │  resolver = { qname: (unit_id, NodeKind) for every unit }
  │
  │ ── pass 2: per-file write ───────────────────────────────────
  │  for each (file_ref, units):
  │    1. reconcile: list_units_for_file → drop obsolete unit_ids
  │    2. PostgresRepo.upsert_units(units)        ← canonical
  │    3. GraphBuilder.build(units, resolver)      ← validated edges
  │       Neo4jRepo.upsert_nodes / upsert_edges
  │    4. VectorRepo.upsert_payloads(VectorPoint[])  ← payload only
  │
  ▼
IngestionResult
  { repo_id, commit_sha, units_collection,
    metrics: {files_walked, units_emitted, nodes_written, …},
    failed_files: [...] }
```

Determinism guarantees:
- File walk order: alphabetical POSIX path
- Unit emission: depth-first AST, sorted by `(line_start, name)`
- Edges: sorted by `(kind, src_id, dst_id)`, deduped before write
- All array fields validated to be sorted + deduped at construction

Phase-3 compression + embeddings are typically run in the same call
chain when the deployment wires them; in the in-process tests we run
Phase-2 + Phase-3 explicitly. See [12_EMBEDDINGS_AND_COMPRESSION](12_EMBEDDINGS_AND_COMPRESSION.md).

---

## Retrieve

Triggered by `POST /retrieve`, the `get_context` MCP tool, or the SDK.

```
client
  │  POST /retrieve { text, repo_id, top_k, … }
  ▼
apps/api/routers/retrieve.py
  │  build RetrievalContext (deterministic query_id = sha256(repo+text))
  ▼
core/retrieval/hybrid_retriever.py · HybridRetriever.run(query)
  │
  │ ── plan ────────────────────────────────────────────────────
  │  QueryPlanner.plan(query) → QueryPlan
  │    use_vector / use_graph / use_metadata flags
  │
  │ ── parallel fan-out ────────────────────────────────────────
  │  asyncio.gather(return_exceptions=True):
  │    GraphRetriever.search(seeds)        → RetrievalCandidate[]
  │    VectorRetriever.search(text, top_k) → RetrievalCandidate[]
  │    MetadataRetriever.search(text)      → RetrievalCandidate[]
  │  failed channels: recorded in `failed_channels`, not raised
  │
  ▼
core/ranking/ranking_model.py · RankingModel.rank(candidates)
  │  fuse channel hits per unit_id
  │  apply mandated formula
  │    0.35·sem + 0.25·graph + 0.20·rec + 0.15·imp + 0.05·feedback
  │  sort: -score, unit_id ASC, file_path ASC
  │  truncate to top_k
  ▼
core/context/context_assembler.py · ContextAssembler.build()
  │  map kind → ContextEntryType
  │  apply priority order (constraints > risks > arch > logic > code)
  │  enforce MAX_CONTEXT_TOKENS budget
  │
  ▼
RetrieveResponse
  { query_id, repo_id, packet: ContextPacket,
    graph_hits, vector_hits, metadata_hits,
    final_candidates, ranked_count, failed_channels, latency_ms }
```

What the agent sees:

- The packet — `ContextPacket` per `RETRIEVAL_SYSTEM_SPEC`
- The trace — channel hit counts + latency + failed channels
- The breakdown — per-entry score + the channels that contributed

Tracing tip: every stage opens an OTEL span and emits a `phase_4` log
event with the same `query_id`. Search by query_id end-to-end.

---

## MCP tool execution

Triggered by `POST /mcp/tools/{name}` from any agent client.

```
agent client
  │  POST /mcp/tools/get_context  (header: X-API-Key)
  ▼
apps/mcp/auth.py · require_mcp_api_key
  │  dev mode if mcp_api_key is unset; otherwise enforce
  ▼
apps/mcp/router.py · invoke_tool
  │  build ExecutionContext (deterministic request_id)
  ▼
core/mcp/execution/tool_executor.py · ToolExecutor.execute()
  │
  │  1. registry lookup  → unknown_tool error (in-band, HTTP 200)
  │  2. validate request → Pydantic (validation_error in-band)
  │  3. tool.execute(request, ctx)
  │       any exception → backend_error in-band (never raises)
  │  4. wrap result into ToolResponse
  │  5. emit mcp_tool_call audit event
  │
  ▼
ToolResponse
  { tool, request_id, status: "success" | "failed",
    data: { ... }, error, error_code, latency_ms, schema_version }
```

Failure model: every error path returns HTTP 200 with
`status: "failed"` + `error_code` so a client can route on `status`
without interpreting HTTP. See [08_MCP_TOOLING](08_MCP_TOOLING.md).

---

## Cross-flow telemetry

Every flow leaves a deterministic trail you can replay:

| Flow | Audit event | Span tree | Log key |
|---|---|---|---|
| Ingest | (Phase 2 has no audit yet) | `ingestion.run` | `phase=phase_2` |
| Retrieve | (audit on the gate, not the read) | `hybrid_retriever.run` | `phase=phase_4 query_id=…` |
| MCP tool | `audit_event{action: <tool>}` | `mcp.tool.execution` | `phase=phase_5 request_id=…` |

Tie them together by `request_id` (MCP), `query_id` (retrieve), or
`unit_id` (ingest).

---

Next: [04 — Installation](04_INSTALLATION.md)
