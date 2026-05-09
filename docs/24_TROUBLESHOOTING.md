# 24 · Troubleshooting

← back to [index](00_INDEX.md) · related: [15_OBSERVABILITY](15_OBSERVABILITY.md), [21_DEPLOYMENT](21_DEPLOYMENT.md), [16_AUDIT_AND_GOVERNANCE](16_AUDIT_AND_GOVERNANCE.md)

A field guide. Scan symptoms top-down; each entry includes the fastest
diagnostic and the root cause when known.

## Cheat-sheet

```bash
# is the service alive
curl -fsS http://localhost:8000/health/live

# is every backend reachable
curl -fsS http://localhost:8000/health/ready | jq '.components[] | select(.status != "ok")'

# full posture
curl -fsS http://localhost:8000/status | jq '{ok: .boot_overall_ok, safe: .safe_mode.enabled, failed: .boot_failed_stages}'

# audit chain integrity
curl -fsS http://localhost:8000/audit/verify | jq

# recent audit events
curl -fsS http://localhost:8000/audit/tail?limit=10 | jq '.entries[] | .payload.action'

# tests
.venv/bin/pytest -q
```

---

## Boot

### `boot_failed_stages: ["storage_init"]`

One of the four storage backends failed `.ping()`. Check
`/health/ready` for the specific component, then:

```bash
docker compose logs postgres neo4j qdrant redis | tail -50
```

Common: Qdrant takes ~10s to become ready; under-provisioned hosts
hit the boot probe before the daemon settles. Restart the api
container after Qdrant logs `ready`.

### `boot_failed_stages: ["audit_chain"]`

The audit logger could not be constructed or `verify_chain()` raised
on an empty chain. Confirm `core/governance/__init__.py` is
importable:

```bash
.venv/bin/python -c "from core.governance import AuditLogger; print('ok')"
```

If this errors, you've broken a Phase-8 contract — pull `main`.

### `safe_mode.enabled: true, triggered_by: "boot_failure"`

Safe-mode auto-engaged. `safe_mode.reason` lists the failed/degraded
stages. Resolve them, then bounce the api process — safe-mode is
process-wide, not auto-recovering.

### Container exits with `FATAL: apps.api.main failed to import`

`scripts/boot.sh` aborted before exec. Most often a missing dep
(`pip install -e .` didn't run) or a Python version mismatch.

---

## Ingest

### `failed_files` non-empty

For each entry in `failed_files`, search the structured log for the
file path:

```bash
docker compose logs api 2>&1 | grep '"file_path":"<path>"'
```

Common causes:

- **Syntax error** — non-Python or pre-3.10 syntax. Inspect the file.
- **OSError on read** — file deleted between walk and parse, or
  permission denied.
- **`EdgeRuleViolation`** — programmer error in graph builder; stop
  the ingest, file a bug.

### `units_changed: 0` after a real edit

The Phase-2 `ON CONFLICT WHERE source_sha differs` guard correctly
detected zero content changes. If you DID change the file, confirm:

```bash
sha256sum <changed-file>   # local hash
psql -c "SELECT source_sha FROM ingestion_units WHERE file_path='<path>' LIMIT 1"
```

If they match, the parser canonicalized the change away (whitespace
only?) or the changed file isn't in the walked set.

### Postgres `relation "ingestion_units" does not exist`

Boot stage 1 ran but `ensure_schema` didn't. Check
`apps/api/lifespan.py` — the call should fire after
`asyncio.gather(*(c.connect() for c in clients))`.

---

## Retrieve

### `vector_hits: 0` for a known-good query

1. Confirm the repo was ingested:
   ```sql
   SELECT count(*) FROM ingestion_units WHERE repo_id = 'X';
   ```
2. Confirm the Qdrant collection has points:
   ```bash
   curl http://localhost:6333/collections/repo:X
   ```
3. Confirm at least one point has `payload.has_vector == true`.
   Phase-2 placeholders are excluded by `VectorRetriever`.

### `failed_channels: ["vector"]`

Qdrant raised. Check the api logs around the failed query_id.
Common: collection doesn't exist (ingest never ran) or qdrant-client
version mismatch (incompatible payload shape).

### Same query gives different scores across runs

Determinism violation. File a bug with:
- the query
- the `repo_id`
- two `RetrieveResponse` JSON dumps
- a `git rev-parse HEAD`

This should be impossible by spec — `test_phase4_golden_packet_is_deterministic_across_runs`
pins it. A regression here is high-priority.

---

## MCP

### `error_code: "validation_error"`

Body shape didn't match the tool's request schema. The error payload
includes a `errors[]` array with `loc / msg / type` per Pydantic.
See [08_MCP_TOOLING](08_MCP_TOOLING.md).

### `error_code: "unknown_tool"`

The tool name isn't in `apps/mcp/registry.py::build_default_registry()`.
Confirm spelling; case-sensitive.

### `error_code: "backend_error"` on `ingest_repository`

The MCP wrapper raised through to the executor's catch-all. The
`error` field includes the original exception type + message. Most
common: the `path` field doesn't exist on the API host.

### HTTP 401 from `/mcp/tools/{name}`

`MCP_API_KEY` is set on the backend but the request omitted or
mismatched the key. Pass via `X-API-Key` or `Authorization: Bearer`.

---

## Audit chain

### `/audit/verify → {"intact": false}`

**Critical.** Treat as a security incident.

1. Snapshot the in-memory chain (`/audit/tail?limit=100000` → file).
2. Snapshot the durable JSONL sink.
3. Compare: the in-memory chain may have been tampered, the JSONL
   may be clean (or vice-versa).
4. Replay the JSONL into a fresh `ImmutableLogStore`; verify.
5. Quarantine any tenant/actor whose entries straddle the broken
   `seq`.

If the JSONL itself is broken, you have a more serious problem —
escalate.

---

## Lifecycle

### Decay plan returns 0 downgrades on a clearly stale system

Two of the three conditions probably aren't met:

- stale: `last_access_at` older than `LIFECYCLE_DECAY_THRESHOLD_DAYS`
- low centrality: `breakdown.centrality < LIFECYCLE_CENTRALITY_THRESHOLD`
- low score: `breakdown.score < LIFECYCLE_LOW_PRIORITY_THRESHOLD`

Inspect `RelevanceBreakdown` for a sample entity to see which signal
is keeping it active.

### Refresh plan triggers continuously

`previous_neighbor_signatures` argument missing → every entity looks
"changed". Persist the previous signatures from the last scan or
pass `{}` only on first run.

---

## Snapshot + replay

### Two builds with same inputs produce different `snapshot_id`

The inputs aren't actually identical. Diff the `components` payload
side-by-side. The most common drift sources:

- `state_version_token` advanced (Redis `INCR` somewhere)
- a new MCP tool registered between builds
- floats in `embeddings` not formatted consistently

### `replay_engine.replay → matches: false`

Either the snapshot is stale (state advanced) or the operation is
non-deterministic. Compute `expected_hash` and `actual_hash` of
intermediate steps to localize the divergence.

---

## UI

### Dashboard pill says `unreachable`

The Next.js process can't proxy to the backend.

```bash
# Confirm proxy target
echo $MEMORY_CL_BACKEND_URL    # default http://localhost:8000
curl -fsS $MEMORY_CL_BACKEND_URL/health/live
```

If the backend is on another host, set `MEMORY_CL_BACKEND_URL` and
restart `npm run dev`.

### Cytoscape graph viewer empty

`query_graph` returned 0 candidates. Check:
- the seed exists (`pkg.utils.add` for the fixture)
- the repo has been ingested into the queried `repo_id`

---

## Tests

### `test_*_is_deterministic_across_runs` flakes

Look for a recently-added module that:
- Reads `datetime.now()` (should be passed in)
- Iterates a `set` or `dict` without sorting
- Imports `random` / `uuid.uuid4()` outside MCP request_id

The CI golden gates pin the determinism contract — flakes here
mean a recent commit broke an invariant.

### Slow tests

The full suite runs in < 1s wall clock. If yours is slow, profile:

```bash
.venv/bin/pytest -q --durations=20
```

Common culprit: a real `asyncio.sleep` snuck into a test instead of
mocked time.

---

## When all else fails

1. `/status` first.
2. `/audit/verify` second.
3. `docker compose logs --since 5m` for the failing service.
4. The relevant golden test in `tests/test_golden_phase*.py` —
   run it locally; if it passes, your environment differs from CI.
5. Open a bug with the `request_id` / `query_id` / `unit_id` you
   were looking at.

---

Next: [25 — Design Decisions](25_DESIGN_DECISIONS.md)
