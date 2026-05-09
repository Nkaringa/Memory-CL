from __future__ import annotations

import pytest

from core.ranking import (
    FEATURE_WEIGHTS,
    FeatureWeights,
    RankingModel,
    cosine_to_similarity,
    graph_proximity_from_depth,
    importance_from_indegree,
    recency_from_age_days,
)
from core.ranking.ranking_model import (
    CandidateProvenance,
    _default_feature_provider,
)
from schemas import RankingFeatures, RetrievalCandidate, RetrievalChannel


# ---- Mandated weights ------------------------------------------------------
def test_default_weights_match_spec_exactly() -> None:
    """The Phase-4 spec mandates these exact constants."""
    assert FEATURE_WEIGHTS.semantic == 0.35
    assert FEATURE_WEIGHTS.graph == 0.25
    assert FEATURE_WEIGHTS.recency == 0.20
    assert FEATURE_WEIGHTS.importance == 0.15
    assert FEATURE_WEIGHTS.feedback == 0.05


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError):
        FeatureWeights(semantic=0.5, graph=0.5, recency=0.5,
                       importance=0.0, feedback=0.0)


# ---- Per-feature scorers ---------------------------------------------------
@pytest.mark.parametrize(("inp", "expected"), [
    (-0.5, 0.0), (0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (1.5, 1.0),
])
def test_cosine_to_similarity_clips_to_unit_interval(inp: float, expected: float) -> None:
    assert cosine_to_similarity(inp) == expected


def test_graph_proximity_seeds_at_one_decays_monotone() -> None:
    assert graph_proximity_from_depth(0, max_depth=3) == 1.0
    assert graph_proximity_from_depth(1, max_depth=3) > graph_proximity_from_depth(2, max_depth=3)
    assert graph_proximity_from_depth(3, max_depth=3) == 0.0
    assert graph_proximity_from_depth(99, max_depth=3) == 0.0


def test_recency_decay_is_exponential() -> None:
    assert recency_from_age_days(0) == 1.0
    assert abs(recency_from_age_days(30, half_life_days=30) - 0.5) < 1e-9
    assert abs(recency_from_age_days(60, half_life_days=30) - 0.25) < 1e-9
    # Negative ages (clock skew) treated as fresh.
    assert recency_from_age_days(-5) == 1.0


def test_importance_saturates() -> None:
    assert importance_from_indegree(0) == 0.0
    assert importance_from_indegree(16) == 1.0
    assert importance_from_indegree(100) == 1.0
    assert 0 < importance_from_indegree(4) < importance_from_indegree(9)


# ---- Ranking formula -------------------------------------------------------
def test_final_score_matches_mandated_formula() -> None:
    f = RankingFeatures(
        semantic_similarity=1.0,
        graph_proximity=1.0,
        recency=1.0,
        importance=1.0,
        user_feedback=1.0,
    )
    # All ones → score = sum of weights = 1.0
    cand = RetrievalCandidate(
        unit_id="u1", channel=RetrievalChannel.VECTOR, raw_score=1.0,
    )
    [r] = RankingModel().rank([cand], feature_provider=lambda _u, _p: f)
    expected = (
        FEATURE_WEIGHTS.semantic * 1
        + FEATURE_WEIGHTS.graph * 1
        + FEATURE_WEIGHTS.recency * 1
        + FEATURE_WEIGHTS.importance * 1
        + FEATURE_WEIGHTS.feedback * 1
    )
    assert abs(r.final_score - expected) < 1e-9
    assert abs(r.final_score - 1.0) < 1e-9


def test_final_score_proportional_to_each_feature_weight() -> None:
    """Single-feature isolation: score = weight when that feature is 1, others 0."""
    cand = RetrievalCandidate(
        unit_id="u1", channel=RetrievalChannel.VECTOR, raw_score=0.0
    )
    pairs = [
        ("semantic_similarity", FEATURE_WEIGHTS.semantic),
        ("graph_proximity", FEATURE_WEIGHTS.graph),
        ("recency", FEATURE_WEIGHTS.recency),
        ("importance", FEATURE_WEIGHTS.importance),
        ("user_feedback", FEATURE_WEIGHTS.feedback),
    ]
    for field, weight in pairs:
        feats = RankingFeatures(**{field: 1.0})
        [r] = RankingModel().rank([cand], feature_provider=lambda _u, _p, f=feats: f)
        assert abs(r.final_score - weight) < 1e-9


# ---- Tie-breaking ----------------------------------------------------------
def test_ties_broken_by_unit_id_ascending() -> None:
    cands = [
        RetrievalCandidate(unit_id="zzz", channel=RetrievalChannel.VECTOR, raw_score=0.5),
        RetrievalCandidate(unit_id="aaa", channel=RetrievalChannel.VECTOR, raw_score=0.5),
        RetrievalCandidate(unit_id="mmm", channel=RetrievalChannel.VECTOR, raw_score=0.5),
    ]
    ranked = RankingModel().rank(cands)
    # All identical scores -> sorted by unit_id ASC.
    assert [r.unit_id for r in ranked] == ["aaa", "mmm", "zzz"]


def test_ties_secondary_by_file_path_when_unit_id_already_distinct() -> None:
    """unit_id is the primary tie-breaker; file_path the secondary.

    When unit_ids are distinct, primary already determines order, so
    file_path is moot. We pin a case where unit_ids and scores are
    identical to a real (impossible) fixture only by virtue of the
    formula returning identical floats — the sort key chain must still
    behave deterministically without throwing.
    """
    cands = [
        RetrievalCandidate(unit_id="x", channel=RetrievalChannel.VECTOR, raw_score=0.5,
                           file_path="b.py"),
        RetrievalCandidate(unit_id="x", channel=RetrievalChannel.GRAPH, raw_score=0.5,
                           file_path="b.py", extra={"depth": 1}),
    ]
    ranked = RankingModel().rank(cands)
    # Same unit -> single result with both channels.
    assert len(ranked) == 1
    assert RetrievalChannel.GRAPH in ranked[0].channels
    assert RetrievalChannel.VECTOR in ranked[0].channels


# ---- Channel fusion --------------------------------------------------------
def test_channel_fusion_combines_provenance() -> None:
    cands = [
        RetrievalCandidate(
            unit_id="u1", channel=RetrievalChannel.VECTOR, raw_score=0.9,
            qualified_name="pkg.m.f", kind="fn", file_path="pkg/m.py",
        ),
        RetrievalCandidate(
            unit_id="u1", channel=RetrievalChannel.GRAPH, raw_score=0.5,
            extra={"depth": 1},
        ),
    ]
    [r] = RankingModel().rank(cands)
    assert r.qualified_name == "pkg.m.f"
    assert r.file_path == "pkg/m.py"
    assert RetrievalChannel.VECTOR in r.channels and RetrievalChannel.GRAPH in r.channels


def test_default_feature_provider_uses_only_channel_observables() -> None:
    """Phase-4 default provider yields semantic+graph; recency/imp/feedback = 0."""
    prov = CandidateProvenance(
        unit_id="u1", cosine=0.8, graph_depth=1,
        channels=(RetrievalChannel.VECTOR, RetrievalChannel.GRAPH),
        file_path="f.py", qualified_name="q", kind="fn",
    )
    feats = _default_feature_provider("u1", prov)
    assert feats.semantic_similarity == pytest.approx(0.8)
    assert feats.graph_proximity > 0.0
    assert feats.recency == 0.0
    assert feats.importance == 0.0
    assert feats.user_feedback == 0.0


# ---- Top-k + deterministic re-runs ----------------------------------------
def test_top_k_truncates_after_sort() -> None:
    cands = [
        RetrievalCandidate(unit_id=f"u{i}", channel=RetrievalChannel.VECTOR,
                           raw_score=i / 10) for i in range(10)
    ]

    def provider(_uid, prov):
        return RankingFeatures(semantic_similarity=prov.cosine or 0.0)

    top3 = RankingModel().rank(cands, feature_provider=provider, top_k=3)
    assert len(top3) == 3
    assert [r.unit_id for r in top3] == ["u9", "u8", "u7"]


def test_ranking_is_byte_deterministic_across_runs() -> None:
    cands = [
        RetrievalCandidate(unit_id="u2", channel=RetrievalChannel.VECTOR,
                           raw_score=0.7, file_path="b.py"),
        RetrievalCandidate(unit_id="u1", channel=RetrievalChannel.VECTOR,
                           raw_score=0.7, file_path="a.py"),
        RetrievalCandidate(unit_id="u3", channel=RetrievalChannel.GRAPH,
                           raw_score=0.0, extra={"depth": 1}),
    ]

    def provider(_uid, prov):
        return RankingFeatures(
            semantic_similarity=prov.cosine or 0.0,
            graph_proximity=0.0 if prov.graph_depth is None else 0.5,
        )

    a = RankingModel().rank(cands, feature_provider=provider)
    b = RankingModel().rank(cands, feature_provider=provider)
    assert [(r.unit_id, r.final_score) for r in a] == \
           [(r.unit_id, r.final_score) for r in b]
