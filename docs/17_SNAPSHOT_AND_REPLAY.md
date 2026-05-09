# 17 · Snapshot + Replay

← back to [index](00_INDEX.md) · related: [16_AUDIT_AND_GOVERNANCE](16_AUDIT_AND_GOVERNANCE.md), [25_DESIGN_DECISIONS](25_DESIGN_DECISIONS.md)

Phase 8's reproducibility layer captures the system's content
fingerprint at a point in time and verifies that re-running an
operation now produces the same output. Source:
`core/reproducibility/`.

## State versioning

`core/reproducibility/state_versioning.py::VersionTokenStore`:

- Per-tenant monotonic counter backed by Redis `INCR`.
- Returns `StateVersion(tenant_id, version="vN", counter=N)`.
- Counter never decreases. `reset()` is exposed for tests only.

`version_token` is what Phase-7 `RetrievalCache` uses for invalidation
([23_PERFORMANCE_AND_SCALING](23_PERFORMANCE_AND_SCALING.md)) and what
the Phase-9 `/snapshot/build` endpoint accepts.

## Snapshot structure

`core/reproducibility/system_snapshot.py::SystemSnapshot`:

```
SystemSnapshot
├── snapshot_id        # SHA-256 of components.to_payload()
├── tenant_id
├── captured_at
├── components: SnapshotComponents
│   ├── graph_state_hash         # nodes + edges → sorted SHA-256
│   ├── embedding_index_hash     # per-vector SHA-256 fingerprints
│   ├── retrieval_config_hash    # FeatureWeights + thresholds
│   ├── schema_version           # global from schemas.base
│   ├── mcp_registry_hash        # tool names + request schemas
│   └── state_version_token      # current Phase-6/8 version
└── metadata
```

Each component is a deterministic SHA-256 over canonical JSON of the
underlying data. The snapshot ID is therefore content-derived.

Same inputs → same `snapshot_id`. Different inputs (anywhere in the
component set) → different ID. Pinned by:
- `test_snapshot_id_is_deterministic_for_same_state`
- `test_snapshot_id_differs_when_state_changes`
- `test_phase8_snapshot_id_byte_deterministic_across_runs`

## Builder

`SystemSnapshotBuilder.build(...)` is a **pure function** of its
inputs — no live storage calls. Callers pass already-materialized
projections of state:

```python
snap = SystemSnapshotBuilder().build(
    tenant_id="acme-corp",
    nodes=graph.nodes, edges=graph.edges,
    embeddings={uid: vec for uid, vec in current_index},
    retrieval_config={"semantic": 0.35, "graph": 0.25, ...},
    mcp_tool_names=["get_context", ...],
    mcp_request_schemas={"get_context": "GetContextRequest", ...},
    state_version_token="v3",
)
```

This is what makes snapshots reproducible: the builder is a
deterministic projection, the inputs are themselves deterministic,
the output is content-hashed.

## Replay engine

`core/reproducibility/replay_engine.py::ReplayEngine`:

```python
result = await engine.replay(
    snapshot, operation, expected_output=...,
)
```

Behavior:

1. Re-runs `operation()` (any async callable producing JSON).
2. Hashes both `expected_output` and the live result via the same
   canonical-JSON path.
3. Returns `ReplayResult(matches, expected_hash, actual_hash, notes)`.

If `matches=False`, either the snapshot is stale (state advanced
legitimately) or the system has drifted in a non-deterministic way
(a bug). Either way, the replay engine produces evidence — an
operator decides which.

## HTTP surface (Phase 9)

| Endpoint | Effect |
|---|---|
| `POST /snapshot/build` | Build a snapshot of the live process; returns `SnapshotResponse` |
| `POST /snapshot/replay` | Compare a `payload` to an `expected_output` — pure JSON-hash equality |

The Phase-9 `/snapshot/build` route currently captures a "boot
snapshot" anchored on the live MCP registry + schema version + tenant
+ state token. It does NOT load the entire graph + embedding index
into memory by default — that would be a multi-GB operation in
production. Operators can call `SystemSnapshotBuilder.build(...)`
directly with materialized projections when they want the full
fingerprint.

## Replay use cases

1. **Compliance** — auditor rebuilds a known good snapshot, replays
   an arbitrary `get_context` payload, confirms identical hash.
2. **Debugging** — "this query returned X yesterday and Y today —
   why?" — replay X against the current snapshot, compare hashes,
   find the drifting component.
3. **Regression test** — golden suites build snapshots in setup,
   run operations, assert replay matches at teardown.

## Determinism guarantees

| Guarantee | Pinned by |
|---|---|
| Same input → same snapshot_id | `test_snapshot_id_is_deterministic_for_same_state` |
| Different input → different snapshot_id | `test_snapshot_id_differs_when_state_changes` |
| Replay matches for deterministic op | `test_replay_engine_reports_match_for_deterministic_op` + `test_phase8_replay_engine_verifies_deterministic_op` |
| Replay detects drift | `test_replay_engine_detects_mismatch` |
| Snapshot covers every state axis | `test_snapshot_components_cover_every_state_axis` |

## Diagnostic integration

`core/diagnostics/corruption_detector.py::CorruptionDetector` rolls
up the audit chain integrity into a single `CorruptionReport` (along
with checksum + graph + schema). If the chain is broken, the
detector flags `audit_chain_intact=False` and the operator should
treat any replay outcome with skepticism until the chain is rebuilt
from the durable JSONL sink.

---

Next: [18 — UI Guide](18_UI_GUIDE.md)
