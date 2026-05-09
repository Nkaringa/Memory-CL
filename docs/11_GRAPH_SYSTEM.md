# 11 · Graph System

← back to [index](00_INDEX.md) · related: [09_RETRIEVAL_SYSTEM](09_RETRIEVAL_SYSTEM.md), [14_DISTRIBUTED_SYSTEM](14_DISTRIBUTED_SYSTEM.md), [03_DATA_FLOW](03_DATA_FLOW.md)

The graph (Phase 2 + Neo4j) is one of three first-class projections
of every ingested repo. It captures **structural** relationships —
DEFINES, IMPORTS, CALLS, INHERITS, REFERENCES, CONTAINS — under a
strict edge-rule contract.

## Node + edge contracts

`schemas/graph.py` defines:

- `NodeKind` — `File | Module | Class | Function | Method | Constant | External`
- `EdgeKind` — `CONTAINS | DEFINES | IMPORTS | CALLS | INHERITS | REFERENCES`
- `EDGE_RULES` — the (`src_kind`, `edge_kind`, allowed_dst_kinds) table
- `is_edge_allowed(src, kind, dst)` — single-source-of-truth gate

Every `GraphNode` carries `node_id` (≡ `unit_id` for non-EXTERNAL),
`kind`, `repo_id`, `qualified_name`, `name`, optional location +
provenance. Frozen.

Every `GraphEdge` carries `src_id`, `kind`, `dst_id`, `repo_id`,
`commit_sha`, optional `weight`. Self-edges rejected at construction.

## Build rules

`core/ingestion/graph_builder.py` is the only writer:

1. File node per unique `repo_id × file_path`.
2. Unit nodes from the IngestionUnits.
3. Structural edges: `File CONTAINS unit`, `parent DEFINES child`.
4. `IMPORTS` edges from module imports (resolves cross-file via the
   global qname resolver; falls back to `External`).
5. `CALLS` edges from function/method bodies (bare-name fallback:
   `<module_qname>.<callee>`).
6. `INHERITS` edges from class bases.

All edges run through `is_edge_allowed()` before write. Violations
raise `EdgeRuleViolation` (fail-fast — programmer error).

## BFS traversal

`core/retrieval/graph_retriever.py::GraphRetriever`:

- Inputs: `seeds: Sequence[str]`, `max_depth: int`.
- Sorted seeds + sorted neighbors per visit → byte-deterministic.
- **Visit-on-pop** — neighbors enter the queue with their `(node, d+1, source_node)` tuple; visited only when popped. This avoids the
  premature-marking bug that would skip expansion of a level.
- Per-call `neighbors()` failure → log warn, drop neighbors, continue.

Depth is bounded by `MAX_GRAPH_TRAVERSAL_DEPTH` (default 3).

## EXTERNAL node handling

Per spec, `External` nodes are the lowest-priority class:

| Layer | Behavior |
|---|---|
| Ingestion | External nodes are MATERIALIZED for every unresolved import / call / base; never delete |
| Graph retriever | External nodes are SKIPPED (never returned as BFS hits) |
| Graph compactor | External nodes are SKIPPED (not compaction candidates) |
| `get_risks` MCP tool | External nodes are SURFACED (the whole point — foreign deps are the risk projection) |
| UI graph viewer | DIMMED with dashed border — visually subordinate |

This makes "external dependency" first-class only when an operator
explicitly asks for it.

## Cross-store identity

`unit_id ≡ node_id ≡ point_id`. Pinned by Phase 2 prep tests
(`test_phase1_compatibility.py::test_unit_id_aligns_with_graph_node_id_convention`).

This means every graph node trivially joins back to its Postgres row
and Qdrant point — no translation tables.

## Sharding

`core/scaling/graph_shard_router.py`:

- shard placement is a pure function of `repo_id` (SHA-256 % shard_count)
- vector router uses the same hash → cross-store joins stay local
- per-repo placement guarantees no cross-shard edges within a repo
  (the spec's "no cross-shard mutation without coordination" rule)

See [14_DISTRIBUTED_SYSTEM](14_DISTRIBUTED_SYSTEM.md).

## Storage

`storage/neo4j_repo.py::Neo4jGraphRepository`:

- per-label uniqueness constraint on `node_id` (created at boot)
- node MERGE pattern: `MERGE (n {node_id: $id}) SET n:Label SET n += $props`
- edge MERGE pattern: `MATCH (a),(b) MERGE (a)-[:KIND]->(b) SET r.repo_id, r.commit_sha, r.weight`
- pre-flight: every edge passes `is_edge_allowed()` against the
  cached node-kind lookup; raises `EdgeNotAllowed` on violation
- file-level reconciliation: `delete_subgraph_for_file` for the
  rare case where a file's symbols moved entirely

## Validation

`core/integrity/graph_validator.py::GraphValidator`:

- **orphan_edge** — edge endpoint not in node set
- **self_edge** — `src_id == dst_id` (should be impossible — schema rejects)
- **edge_rule** — edge kind invalid for the given src/dst
- **id_mismatch** — `node_id != unit_id` for a non-EXTERNAL node

Used by Phase-8 `CorruptionDetector` and the diagnostics page.

## UI

The Phase-10 `/graph` page renders the BFS output with Cytoscape +
`fcose` layout. EXTERNAL nodes get dashed borders + reduced opacity.
The seed (`depth=0`) is colored with the accent.

---

Next: [12 — Embeddings + Compression](12_EMBEDDINGS_AND_COMPRESSION.md)
