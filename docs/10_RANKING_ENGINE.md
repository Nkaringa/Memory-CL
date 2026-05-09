# 10 · Ranking Engine

← back to [index](00_INDEX.md) · related: [09_RETRIEVAL_SYSTEM](09_RETRIEVAL_SYSTEM.md), [13_MEMORY_EVOLUTION](13_MEMORY_EVOLUTION.md)

The ranking model is **mandated**. The weights below are pinned by
spec and enforced by `FeatureWeights.__post_init__` (raises if the
sum drifts from 1.0 by more than 1e-9).

## Formula

```
FinalScore = 0.35 · semantic_similarity
           + 0.25 · graph_proximity
           + 0.20 · recency
           + 0.15 · importance
           + 0.05 · user_feedback
```

Each input is in `[0, 1]`. Output is in `[0, 1]` (clamped).

Source: `core/ranking/ranking_model.py::_final_score`.

## Per-feature scorers

Source: `core/ranking/scoring.py`.

### Semantic similarity

```python
cosine_to_similarity(cosine: float) -> float
```

Clips negative cosines to 0; clamps to [0, 1]. Vectors are
L2-normalized so cosine ≈ inner product.

### Graph proximity

```python
graph_proximity_from_depth(depth: int, max_depth: int) -> float
```

- `depth = 0` (the seed) → 1.0
- `depth ≥ max_depth` → 0.0
- linear taper otherwise

Depth-only; no per-edge weight, no PRNG → deterministic.

### Recency

```python
recency_from_age_days(age_days: float, *, half_life_days: float = 30.0) -> float
```

Exponential decay: `0.5 ^ (age / half_life)`. Negative ages (clock
skew) treated as fresh (1.0).

### Importance

```python
importance_from_indegree(in_degree: int, *, saturate_at: int = 16) -> float
```

Saturating sqrt curve: 0-references → 0, ≥ 16-references → 1.0.

### User feedback

Phase-4 ships zero by default; Phase-6 `RetrievalFeedbackCollector`
provides the signal Phase 11+ will plug in here.

## Tie-breaking

Mandated sort key:

```
(-final_score, unit_id ASC, file_path ASC)
```

If two entries have identical scores, the lexicographically smaller
`unit_id` wins; if those also collide, the smaller `file_path` wins.
Stable, deterministic, replay-safe.

Pinned by `test_ties_broken_by_unit_id_ascending` and
`test_ranking_is_byte_deterministic_across_runs`.

## Channel fusion

Before ranking, candidates from the three retrieval channels are
fused into one slot per `unit_id`:

```python
{
  "cosine":         <best cosine seen>,    # vector channel
  "graph_depth":    <smallest depth seen>,  # graph channel
  "channels":       {"vector", "graph", ...},
  "file_path", "qualified_name", "kind":
                    <first non-None observed across channels>,
}
```

Source: `RankingModel._group_by_unit`.

The fused `CandidateProvenance` feeds a `feature_provider(unit_id, prov) → RankingFeatures`.
The default provider:

- semantic = `cosine_to_similarity(cosine)` if cosine present, else 0
- graph = `graph_proximity_from_depth(graph_depth, max_depth=3)` if depth present, else 0
- recency / importance / feedback = 0 (Phase-6 hooks fill these later)

Custom providers can be threaded through `RankingModel.rank(..., feature_provider=...)`.

## API surface

```python
RankingModel(weights: FeatureWeights | None = None)
ranked: list[RankedResult] = model.rank(
    candidates,
    *,
    feature_provider=None,   # default = channel-only
    top_k=10,
    query_id="...",
    repo_id="...",
)
```

`RankedResult` carries:
- `unit_id`, `final_score`, `breakdown` (full `RankingFeatures`)
- `channels`, `file_path`, `qualified_name`, `kind`

## Observability

- OTEL span: `ranking_engine.score`
- Log: `phase=phase_4 event=ranking_run count=N`

## Why these weights

- **Semantic dominates** — 0.35 — because text similarity is the
  highest-recall signal once embeddings exist.
- **Graph contributes meaningfully** — 0.25 — because structurally
  related code is what an agent usually wants when it asks about a
  symbol.
- **Recency exists but doesn't dominate** — 0.20 — because Memory-CL
  ingests deterministically, not historically.
- **Importance** — 0.15 — in-degree as a relevance proxy.
- **Feedback reserved** — 0.05 — kept tiny so Phase-6 can experiment
  without breaking the existing rank order.

These weights are NOT runtime-configurable (would invalidate every
prior result). To change them, bump `SCHEMA_VERSION` first and write
a migration.

---

Next: [11 — Graph System](11_GRAPH_SYSTEM.md)
