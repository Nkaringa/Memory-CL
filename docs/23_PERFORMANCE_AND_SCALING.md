# 23 · Performance + Scaling

← back to [index](00_INDEX.md) · related: [14_DISTRIBUTED_SYSTEM](14_DISTRIBUTED_SYSTEM.md), [15_OBSERVABILITY](15_OBSERVABILITY.md), [21_DEPLOYMENT](21_DEPLOYMENT.md)

Phase 7 ships the production performance toolkit: a versioned cache,
backpressure, rate limiting, batching, and a worker pool. This page
is the operator's tuning reference.

## Caching

### `core/scaling/retrieval_cache.py::RetrievalCache`

LRU + TTL with **version-aware invalidation**.

- Key = `cache_key_for_query(repo_id, text, top_k, kinds, seeds, version_token)`.
- Same semantically-equal query → same key (sorted+joined components).
- `get(key, version_token)` returns `None` if the entry's version
  doesn't match the supplied token → automatic eviction on lifecycle
  bumps.
- `invalidate_version(version_token)` removes every entry stored
  under that token (Phase-6 lifecycle calls this when relevance
  changes).

### Tuning

| Knob | Default | Trade-off |
|---|---|---|
| `SCALE_RETRIEVAL_CACHE_SIZE` | 1024 entries | Memory vs hit rate |
| `SCALE_RETRIEVAL_CACHE_TTL_SECONDS` | 300 | Staleness vs hit rate |

Hit-rate metric: `cache.hits / (cache.hits + cache.misses)`. Surface
in your own dashboard from the `RetrievalCache` instance.

## Batching

### `core/performance/batching_engine.py::BatchingEngine[ItemT, ResultT]`

Generic micro-task batcher with two flush triggers:

- size (`max_size` items), OR
- time (`max_wait_ms` since first item)

Per-item futures resolve when the batch processor returns.
Misaligned result counts raise `ValueError`. Failures fan out to
every caller.

### Tuning

| Knob | Default | Trade-off |
|---|---|---|
| `SCALE_BATCH_MAX_SIZE` | 32 | Throughput vs latency |
| `SCALE_BATCH_MAX_WAIT_MS` | 20 | Fairness under low load |

Bigger batches = better backend efficiency, worse first-byte latency.
20ms is a good default for embed-on-write workloads.

## Rate limiting

### `core/performance/rate_limiter.py::RateLimiter`

Token bucket per `(caller, resource)`. Time threaded in by callers,
so two replays produce identical allow/deny decisions.

| Knob | Default | Notes |
|---|---|---|
| `SCALE_DEFAULT_RATE_PER_SECOND` | 20 | Requests per second per bucket |
| `burst` (constructor) | `rate_per_second` | Tokens accrued before throttling |

Use this to protect any per-caller resource — typically MCP tool
invocations per agent. Wire into a FastAPI dependency for
per-route enforcement.

## Backpressure

### `core/performance/backpressure_controller.py::BackpressureController`

Mandated escalation order:

| Level | Throttles |
|---|---|
| 0 NONE | nothing |
| 1 INGESTION | ingestion only |
| 2 + RETRIEVAL | + retrieval fan-out |
| 3 + MCP | + MCP execution |

The **graph layer is never throttled**. Spec invariant.

| Knob | Default | Notes |
|---|---|---|
| `SCALE_BACKPRESSURE_THRESHOLD` | 0.8 | queue ratio that triggers level 1 |

Levels 2, 3 trigger at 1.5× and 2× the threshold respectively.

`BackpressureController.evaluate(...)` is pure — feed it observed
queue depths, get back a snapshot. Wire snapshots into your
admission control loop.

## Sharding

`core/scaling/{graph,vector}_shard_router.py` + `infra/distributed/shard_manager.py`.

- Per-repo placement (deterministic SHA-256 % shard_count).
- Vector + graph routers use the same hash → cross-store joins
  stay local on the same shard index.
- See [14_DISTRIBUTED_SYSTEM](14_DISTRIBUTED_SYSTEM.md) for full
  topology + replica mapping.

| Knob | Default | Notes |
|---|---|---|
| `SCALE_SHARD_COUNT` | 4 | Increase only with backend re-sharding plan |

**Re-sharding is not yet automated.** Increasing `SCALE_SHARD_COUNT`
on a populated system would re-route requests but the data wouldn't
move. Plan for an offline migration when scaling out.

## Worker pool

`infra/distributed/worker_pool.py::WorkerPool`:

| Knob | Default | Notes |
|---|---|---|
| `SCALE_WORKER_COUNT` | 4 | Bounds in-flight tasks |
| `max_retries` | 3 | Per `submit` call |
| `backoff_base_ms` | 50 | Exponential base |
| `backoff_factor` | 2.0 | Geometric multiplier |

Stats live on `pool.stats` (snapshot copy).

## Common bottlenecks

| Symptom | Likely cause | Where to look |
|---|---|---|
| Retrieval p95 > 500ms | cold cache OR expensive Qdrant query | `RetrievalCache` hit rate; Qdrant collection size |
| Ingest backs up | Postgres write contention | upsert RPS vs `pool_size`; consider larger pool |
| MCP request_id timeouts | upstream backend slow | `latency_tracker.snapshot(metric="metadata", ...)` per shard |
| Memory growing | unbounded audit chain in-process | confirm `JsonlFileAuditSink` rotates / flushes |
| Backpressure stuck at level 3 | runaway producer | inspect `BackpressureSnapshot.triggers` |

## Determinism caveats

The `WorkerPool` retry sequence is deterministic in attempt count
but not in wall-clock — `await asyncio.sleep(...)` introduces
real-time delays. For replay purposes, treat retry timing as a
liveness concern, not a correctness one. The actual computed
results remain byte-deterministic.

Backpressure decisions are deterministic given the observed
ratios — the inputs (queue depth, inflight count) are not.

## Capacity sizing (rule of thumb)

| Tier | Per shard / replica | Comment |
|---|---|---|
| api | 2 cores, 1 GB | Stateless |
| worker | 2 cores, 1 GB per concurrency | Match `SCALE_WORKER_COUNT` |
| postgres | 4 cores, 8 GB, NVMe | Largest tier — canonical store |
| neo4j | 4 cores, 8 GB | Per shard |
| qdrant | 4 cores, 8 GB | Per shard; scale RAM with vector count |
| redis | 2 cores, 2 GB | Cluster for HA |

Storage scales linearly with units ingested. Plan for ~2 KB per
unit on Postgres + ~6 KB per unit on Neo4j + `vector_size × 4 B`
per vector on Qdrant + ~200 B per lifecycle key on Redis.

---

Next: [24 — Troubleshooting](24_TROUBLESHOOTING.md)
