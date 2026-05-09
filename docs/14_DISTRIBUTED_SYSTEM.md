# 14 · Distributed System

← back to [index](00_INDEX.md) · related: [11_GRAPH_SYSTEM](11_GRAPH_SYSTEM.md), [23_PERFORMANCE_AND_SCALING](23_PERFORMANCE_AND_SCALING.md), [21_DEPLOYMENT](21_DEPLOYMENT.md)

Phase 7 turns the engine into a horizontally scalable runtime
without changing any Phase 1–6 semantics. Source:
`infra/distributed/` and `core/scaling/`.

## Sharding

### `core/scaling/graph_shard_router.py` and `vector_shard_router.py`

Deterministic SHA-256 % `shard_count` placement keyed by `repo_id`.

Both routers use the **same hash** so the graph node and the vector
point for the same `unit_id` always co-locate on the same shard
index. This is what lets cross-store joins stay local.

```python
shard_idx = int.from_bytes(sha256(repo_id)[:8], "big") % shard_count
graph_shard_id   = f"graph-{shard_idx}"
vector_shard_id  = f"vector-{shard_idx}"
vector_collection = f"repo:{repo_id}::s{shard_idx}"
```

API:
- `route(repo_id) → ShardAssignment`
- `route_node(repo_id, node_id) → ShardAssignment` — `node_id` is
  intentionally ignored (per-repo placement is the spec invariant).

### `infra/distributed/shard_manager.py`

`ShardTopology.round_robin(shard_count, replicas)` builds a
shard → replica mapping. `ShardManager.replica_for(repo_id)`
combines the router's shard placement with the topology's
shard → replica map.

## Worker pool

`infra/distributed/worker_pool.py::WorkerPool`:

- Bounded asyncio concurrency via `Semaphore(worker_count)`.
- Exponential backoff retry (deterministic — depends only on the
  attempt number).
- `WorkerStats` snapshot for observability:
  `submitted / completed / failed / retried / inflight`.
- `pool.map(fn, items)` fan-out + gather; failures bubble up.
- `pool.submit(fn, *args)` single submission with retry.

## Task scheduler

`infra/distributed/task_scheduler.py::TaskScheduler`:

- Priority queue (heapq, min-heap) keyed by `(-priority, seq)`.
- Higher-priority tasks dispatch first; FIFO within a priority band.
- Dispatches through the underlying `WorkerPool` so global
  concurrency stays bounded.
- Inflight task references held to prevent GC race during fan-out.

`TaskPriority` enum: `BACKGROUND(0) | NORMAL(5) | HIGH(10) | CRITICAL(15)`.

## Load balancer

`infra/distributed/load_balancer.py::LoadBalancer`:

- `RoutingStrategy.HASH` — deterministic by key (e.g. `repo_id`).
  Identical requests always hit the same replica.
- `RoutingStrategy.ROUND_ROBIN` — strict cycle counter; useful for
  stateless probes.
- Replicas are sorted at construction so identical inputs across
  processes route identically.

## Backpressure

`core/performance/backpressure_controller.py::BackpressureController`:

Mandated escalation order:

| Level | Throttles |
|---|---|
| `NONE` | nothing |
| `INGESTION` | ingestion only |
| `INGESTION_AND_RETRIEVAL` | + retrieval fan-out |
| `INGESTION_AND_RETRIEVAL_AND_MCP` | + MCP execution |

The **graph layer is never throttled**, regardless of level. This is
the spec's "NEVER drop graph integrity" invariant — pinned by
`test_backpressure_never_throttles_graph_layer`.

`BackpressureController.evaluate(queue_depth, queue_capacity, inflight, inflight_capacity)`
returns a `BackpressureSnapshot` with the level, the triggers, and
the observed ratios. Pure function over the inputs.

## Rate limiting

`core/performance/rate_limiter.py::RateLimiter`:

- Token bucket per `(caller, resource)`.
- Time threaded in by callers — same request stream → same allow/deny
  decisions.
- Default: `rate_per_second=20, burst=rate_per_second`.

## Batching

`core/performance/batching_engine.py::BatchingEngine[ItemT, ResultT]`:

- Generic over item + result types.
- Two flush triggers: `max_size` items OR `max_wait_ms` since first item.
- Per-item futures wired off a single `process_batch(items) → list[ResultT]` call.
- Failures fan out to every caller in the batch.
- Misaligned result counts raise `ValueError`.

## Distributed ingestion

`core/scaling/ingestion_distributor.py::IngestionDistributor`:

Pure planner — given a list of repos, returns a
`DistributedIngestionPlan` mapping each repo to its
graph + vector shard. No I/O. Sorted by `repo_id` for determinism.

`apps/api/routers/ingest.py` does NOT consume this directly today —
the planner is provisioned for Phase-11 multi-repo batch ingest.

## Failure model

| Failure | Behavior |
|---|---|
| Worker task raises | `WorkerStats.failed += 1`; with `max_retries > 0`, exponential backoff retry; on exhaustion, raises out |
| Single shard offline | `ShardManager.replica_for` raises `KeyError` — caller decides retry / fail-over |
| Backend overloaded | Backpressure escalates; graph layer always served |
| Audit chain unavailable | Phase-9 boot stage 7 fails; safe-mode auto-enabled |

## Determinism in distributed mode

Shard placement, retry sequencing, batch ordering, and load-balancer
hashing are all deterministic given the same inputs. Pinned by:
- `test_phase7_shard_assignment_is_byte_deterministic`
- `test_rate_limiter_is_deterministic_for_identical_streams`

---

Next: [15 — Observability](15_OBSERVABILITY.md)
