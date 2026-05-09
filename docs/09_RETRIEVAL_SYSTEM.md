# 09 · Retrieval System

← back to [index](00_INDEX.md) · related: [10_RANKING_ENGINE](10_RANKING_ENGINE.md), [11_GRAPH_SYSTEM](11_GRAPH_SYSTEM.md), [12_EMBEDDINGS_AND_COMPRESSION](12_EMBEDDINGS_AND_COMPRESSION.md), [03_DATA_FLOW](03_DATA_FLOW.md)

Phase 4 implements a **hybrid retrieval engine** with three channels
fused by a deterministic ranking model. Source:
`core/retrieval/`.

## Channels

### Vector retriever (`core/retrieval/vector_retriever.py`)

- Cosine top-k against the per-repo Qdrant collection.
- Embeds the query via the configured `Embedder` (default:
  Phase-3 `DeterministicEmbedder`, hash-based + L2-normalized).
- Filters Phase-2 placeholder points (`payload.has_vector == false`).
- Negative cosines clipped to 0; results re-sorted by `(-score, id)`
  to defeat backend-specific tie-breakers.
- Backend exceptions → `[]` + warning log; never raised.

### Graph retriever (`core/retrieval/graph_retriever.py`)

- Bounded BFS via `GraphRepository.neighbors()`.
- **Visit-on-pop** semantics — neighbors discovered at depth `d`
  enter the queue with `(node, d+1, source_node)` and are visited
  when popped. Prevents the "marked-too-early-and-skipped" bug.
- EXTERNAL nodes skipped per spec ([11_GRAPH_SYSTEM](11_GRAPH_SYSTEM.md)).
- Per-call neighbor failure → log warning, drop neighbors, continue.
- Sorted seeds + sorted neighbors → deterministic candidate order.

### Metadata retriever (`core/retrieval/metadata_retriever.py`)

- Postgres ILIKE on `qualified_name / name / docstring / signature`.
- ORDER BY `updated_at DESC, unit_id ASC` (deterministic).
- Hard `LIMIT` per `top_k`.
- Backend exceptions → `[]` + warning log.

## Hybrid orchestration

`core/retrieval/hybrid_retriever.py`:

```python
plan = QueryPlanner().plan(query)         # which channels to fire
results = await asyncio.gather(           # parallel fan-out
    vector.search(...), graph.search(...), metadata.search(...),
    return_exceptions=True,                # failure isolation
)
candidates = fuse_per_unit_id(results)    # one slot per unit
ranked = RankingModel().rank(candidates)  # mandated formula
packet = ContextAssembler().build(ranked) # priority + budget
```

`HybridRetrievalResult` carries:
- `candidates` (sorted by `(channel, unit_id)`)
- `graph_hits / vector_hits / metadata_hits` (per-channel counts)
- `failed_channels` (any channel whose task raised)
- `latency_ms`

## Query planner

`core/retrieval/query_planner.py::QueryPlanner` is the deterministic
"what to fire" decision:

- Vector + metadata: always on
- Graph: on iff caller supplied `seed_unit_ids`

Future planners can wire learned heuristics behind this surface
without touching the channel retrievers.

## Filter knobs

- `unit_kinds` (e.g. `["fn", "cls"]`) — applied client-side in the
  vector retriever; metadata retriever passes it as a SQL `ANY()`.
- `seed_unit_ids` — graph BFS roots.
- `top_k` — applied per channel and again at the ranking layer.

## Failure model

| Channel raises | Behavior |
|---|---|
| Vector | `[]`, `failed_channels: ["vector"]`, log warn |
| Graph | per-seed; partial results returned |
| Metadata | `[]`, `failed_channels: ["metadata"]`, log warn |

Hybrid never re-raises. Tested by:
- `test_hybrid_isolates_channel_failure`
- `test_phase7_backpressure_escalates_under_load_but_spares_graph`

## Determinism

- Same query + same backend state → byte-identical
  `RetrieveResponse` modulo `latency_ms`.
- Pinned by `test_phase4_golden_packet_is_deterministic_across_runs`.

## Cache integration (Phase 7)

`core/scaling/retrieval_cache.py::RetrievalCache` is wired by the
operator (not auto-mounted on `/retrieve` to keep the contract
simple). Key = `cache_key_for_query(repo_id, text, top_k, kinds, seeds, version_token)`.
Phase-6 lifecycle bumps `version_token` to invalidate.

See [23_PERFORMANCE_AND_SCALING](23_PERFORMANCE_AND_SCALING.md).

## What lives where

| File | Role |
|---|---|
| `core/retrieval/query_planner.py` | Channel selection |
| `core/retrieval/graph_retriever.py` | BFS channel |
| `core/retrieval/vector_retriever.py` | Cosine channel |
| `core/retrieval/metadata_retriever.py` | Postgres channel |
| `core/retrieval/hybrid_retriever.py` | Orchestrator |
| `core/retrieval/context.py` | `RetrievalContext` (deterministic query_id) |
| `core/retrieval/logevent.py` | `emit_phase4_event` (spec-mandated `retrieval_run`) |

---

Next: [10 — Ranking Engine](10_RANKING_ENGINE.md)
