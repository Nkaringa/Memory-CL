from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.lifecycle import RelevanceInputs, RelevanceScorer
from core.lifecycle.relevance_scorer import (
    CENTRALITY_WEIGHT,
    RECENCY_WEIGHT,
    SUCCESS_WEIGHT,
    USAGE_WEIGHT,
)


def test_mandated_weights_match_spec_exactly() -> None:
    assert USAGE_WEIGHT == 0.4
    assert RECENCY_WEIGHT == 0.3
    assert CENTRALITY_WEIGHT == 0.2
    assert SUCCESS_WEIGHT == 0.1
    total = USAGE_WEIGHT + RECENCY_WEIGHT + CENTRALITY_WEIGHT + SUCCESS_WEIGHT
    assert pytest.approx(total, abs=1e-9) == 1.0


def test_zero_inputs_yield_zero_score() -> None:
    now = datetime.now(UTC)
    out = RelevanceScorer().score(RelevanceInputs("u1"), now=now)
    assert out.score == 0.0
    assert out.usage == 0.0
    assert out.recency == 0.0
    assert out.centrality == 0.0
    assert out.success == 0.0


def test_max_inputs_yield_unit_score() -> None:
    now = datetime.now(UTC)
    out = RelevanceScorer(usage_saturate_at=4, centrality_saturate_at=4).score(
        RelevanceInputs(
            entity_id="u1",
            usage_count=100,
            last_access_at=now,
            graph_in_degree=100,
            retrieval_attempts=10,
            retrieval_successes=10,
        ),
        now=now,
    )
    assert pytest.approx(out.score, abs=1e-6) == 1.0


def test_recency_decays_exponentially() -> None:
    now = datetime.now(UTC)
    scorer = RelevanceScorer(usage_window_days=30)
    fresh = scorer.score(RelevanceInputs("u", last_access_at=now), now=now)
    old = scorer.score(
        RelevanceInputs("u", last_access_at=now - timedelta(days=30)),
        now=now,
    )
    older = scorer.score(
        RelevanceInputs("u", last_access_at=now - timedelta(days=60)),
        now=now,
    )
    assert fresh.recency > old.recency > older.recency
    assert pytest.approx(old.recency, abs=1e-6) == 0.5


def test_score_is_deterministic_for_same_inputs() -> None:
    now = datetime.now(UTC)
    s = RelevanceScorer()
    a = s.score(
        RelevanceInputs(
            "u", usage_count=5, last_access_at=now,
            graph_in_degree=3, retrieval_attempts=8, retrieval_successes=6,
        ),
        now=now,
    )
    b = s.score(
        RelevanceInputs(
            "u", usage_count=5, last_access_at=now,
            graph_in_degree=3, retrieval_attempts=8, retrieval_successes=6,
        ),
        now=now,
    )
    assert a == b


def test_score_components_proportional_to_weights() -> None:
    """Single-feature isolation: setting only one signal high yields
    the corresponding weight as the score."""
    now = datetime.now(UTC)
    s = RelevanceScorer(usage_saturate_at=1, centrality_saturate_at=1)

    only_usage = s.score(
        RelevanceInputs("u", usage_count=10), now=now,
    ).score
    only_recency = s.score(
        RelevanceInputs("u", last_access_at=now), now=now,
    ).score
    only_centrality = s.score(
        RelevanceInputs("u", graph_in_degree=10), now=now,
    ).score
    only_success = s.score(
        RelevanceInputs("u", retrieval_attempts=5, retrieval_successes=5),
        now=now,
    ).score

    assert pytest.approx(only_usage, abs=1e-6) == USAGE_WEIGHT
    assert pytest.approx(only_recency, abs=1e-6) == RECENCY_WEIGHT
    assert pytest.approx(only_centrality, abs=1e-6) == CENTRALITY_WEIGHT
    assert pytest.approx(only_success, abs=1e-6) == SUCCESS_WEIGHT
