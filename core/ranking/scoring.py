"""Per-feature scoring functions.

Each function maps an external observable (cosine score, graph depth,
commit age, in-degree count) into the canonical [0, 1] interval used
by the ranking formula. Functions are pure and deterministic.
"""

from __future__ import annotations

import math


def cosine_to_similarity(cosine: float) -> float:
    """Clamp cosine score into [0, 1].

    Vector embeddings here are L2-normalized so cosine ∈ [-1, 1]; we
    rescale to [0, 1] (1 means identical, 0 means orthogonal-or-opposite).
    Negative cosines get clipped at 0 — a negative correlation is no
    more useful for retrieval than no correlation.
    """
    if cosine < 0.0:
        return 0.0
    if cosine > 1.0:
        return 1.0
    return cosine


def graph_proximity_from_depth(depth: int) -> float:
    """Convert BFS depth to a (0, 1] proximity score: `1 / (1 + depth)`.

    depth=0 (the seed itself) → 1.0
    depth=1 → 0.5
    depth=2 → 0.333…
    This is the contract documented in `schemas/retrieval.py` for the
    graph channel's raw_score. The decay is monotone-decreasing,
    depth-only, and never hard-zeroes — the previous
    `1 - depth/max_depth` taper scored every candidate AT the requested
    depth as exactly 0.0, which was a bug. Traversal bounds belong to
    the retriever, not the score.
    """
    if depth <= 0:
        return 1.0
    return 1.0 / (1.0 + depth)


def recency_from_age_days(age_days: float, *, half_life_days: float = 30.0) -> float:
    """Exponential decay over commit age.

    `0.5 ** (age / half_life)` puts a fresh commit at 1.0, a 30-day-old
    commit at 0.5, a 60-day-old commit at 0.25, etc. Negative ages
    (clock skew) are treated as fresh.
    """
    if age_days <= 0:
        return 1.0
    if half_life_days <= 0:
        return 0.0
    return 0.5 ** (age_days / half_life_days)


def importance_from_indegree(in_degree: int, *, saturate_at: int = 16) -> float:
    """Map graph in-degree to [0, 1] using a saturating curve.

    A symbol nobody references scores 0; a symbol referenced by 16+
    others scores 1.0. The square-root keeps the bottom of the range
    informative — going from 0 to 1 reference is the most signal.
    """
    if in_degree <= 0 or saturate_at <= 0:
        return 0.0
    if in_degree >= saturate_at:
        return 1.0
    return math.sqrt(in_degree / saturate_at)
