# 13 · Memory Evolution

← back to [index](00_INDEX.md) · related: [12_EMBEDDINGS_AND_COMPRESSION](12_EMBEDDINGS_AND_COMPRESSION.md), [10_RANKING_ENGINE](10_RANKING_ENGINE.md), [16_AUDIT_AND_GOVERNANCE](16_AUDIT_AND_GOVERNANCE.md)

Phase 6 is the **lifecycle layer**. It turns the static memory engine
into a living one: signals are collected, relevance is scored,
decisions are planned. Every decision is downgrade-only — Memory-CL
**never deletes data**.

## Relevance score

`core/lifecycle/relevance_scorer.py` — mandated formula:

```
RelevanceScore = 0.4 · usage_frequency
               + 0.3 · recency
               + 0.2 · graph_centrality
               + 0.1 · retrieval_success_rate
```

Per-feature scorers:

- `_usage_score(count, saturate_at)` — saturating sqrt curve
- `_recency_score(last_access_at, now, half_life_days)` — exponential decay
- `_centrality_score(in_degree, saturate_at)` — saturating sqrt
- `_success_rate(attempts, successes)` — clipped to [0, 1]

`now` is **passed in by the caller** (LifecycleContext), not read
from `datetime.now()` — required for deterministic re-runs.

## Analytics signals

`core/analytics/`:

- `usage_tracker.py` — Redis INCR counter + `last_access_at`
  timestamp per `(repo_id, entity_id)`. Append-only.
- `retrieval_feedback_collector.py` — append-only `attempts` +
  `successes` counters per entity. Failed outcomes never decrement.
- `performance_analyzer.py` — proposes adjusted `FeatureWeights`
  from observed `PerformanceSignals` (vector / graph / metadata
  success rates + feedback volume). Bounded `max_drift`. Output
  renormalized to `sum=1.0`. Returns the baseline verbatim when
  `feedback_volume == 0`.

The proposed weights are CONSUMABLE by `RankingModel(weights=...)` —
they do NOT replace the mandated defaults. Operators can A/B them.

## Decay engine

`core/lifecycle/decay_engine.py::DecayEngine` produces a
`DecayPlan` (deterministic; sorted by `entity_id`):

| Action | Trigger |
|---|---|
| `NO_OP` | none of the conditions met |
| `DOWNGRADE` | active AND stale (> N days) AND low centrality AND low score |
| `PROMOTE` | currently low_priority AND score recovered above threshold |

Apply path:

- Soft mutation only — flips a `phase6:status:<repo>:<entity>` Redis key.
- Never touches Postgres / Neo4j / Qdrant.
- Emits an audit event before each `client.set`.
- Plan can be dry-run (`apply=False`); applied separately when the
  operator is ready.

## Memory compactor

`core/lifecycle/memory_compactor.py::MemoryCompactor`:

- Identifies units below `low_priority_threshold` (excludes classes
  by design — class membership matters even when usage is low).
- Folds victims into a per-module `DenseModule` summary via the
  Phase-3 `ModuleSummarizer`.
- Output is a `CompactionPlan` — non-destructive. Applying the plan
  is operator-initiated and out of Phase-6 scope.

## Graph compactor

`core/lifecycle/graph_compactor.py::GraphCompactor`:

- Candidates: leaf kinds (Function / Method / Constant) below
  `centrality_threshold`.
- Plan: fold each leaf into its enclosing module's node, with edges
  REWRITTEN to terminate at the module aggregate.
- Self-edges from the rewrite are dropped.
- **Plan only** — Phase-2 graph storage is not mutated by Phase 6.

## Embedding refresh scheduler

`core/lifecycle/embedding_refresh_scheduler.py`:

Trigger conditions (all per-spec):
- `LOW_RELEVANCE` — score < `refresh_threshold`
- `NEIGHBOR_DRIFT` — current vs previous neighborhood signature differ
- `LOW_SUCCESS_RATE` — feedback success_rate < 0.5

Output: `RefreshPlan` listing entities + reasons (sorted alphabetically).
Caller decides when to trigger Phase-3 embedding pipeline against
the plan's `to_refresh` list.

## State scanner

`core/lifecycle/state_scanner.py::LifecycleStateScanner` orchestrates
one pass:

1. Pull usage + feedback signals (analytics layer).
2. Compute graph in-degree.
3. Score every node via `RelevanceScorer.score(...)`.
4. Run decay → memory compaction → graph compaction → refresh planners.
5. Return one `LifecycleScanResult`.

Pinned `LifecycleContext.now` ensures the entire pass is deterministic
for a given state snapshot. Pinned by:
- `test_phase6_golden_scan_is_byte_deterministic_across_runs`

## What this layer does NOT do

- **Delete data.** Ever. Apply paths only flip Redis flags or write
  derived plans.
- **Re-rank live retrieval.** Lifecycle proposes adjustments; the
  retrieval path remains the mandated formula.
- **Apply graph compaction.** Phase-2 graph schemas are immutable.

Phase 11+ may surface "apply" buttons in the operator UI; for now
the plans are diagnostic outputs.

---

Next: [14 — Distributed System](14_DISTRIBUTED_SYSTEM.md)
