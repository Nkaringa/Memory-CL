from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import Settings
from schemas import (
    ContextEntry,
    ContextPacket,
    Query,
    RankedResult,
    RankingFeatures,
    RetrievalCandidate,
    RetrievalChannel,
)


def test_settings_expose_phase4_knobs() -> None:
    s = Settings()
    assert s.max_graph_traversal_depth >= 1
    assert s.default_top_k >= 1


def test_query_validates_and_dedupes() -> None:
    q = Query(
        text="auth flow",
        repo_id="acme",
        unit_kinds=["fn", "fn", "cls"],
        seed_unit_ids=["b", "a", "a"],
    )
    assert q.unit_kinds == ["cls", "fn"]
    assert q.seed_unit_ids == ["a", "b"]


def test_query_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        Query(text="", repo_id="r")


def test_ranking_features_clamped_to_unit_interval() -> None:
    with pytest.raises(ValidationError):
        RankingFeatures(semantic_similarity=2.0)
    with pytest.raises(ValidationError):
        RankingFeatures(graph_proximity=-0.1)


def test_retrieval_candidate_carries_channel_provenance() -> None:
    c = RetrievalCandidate(
        unit_id="u1",
        channel=RetrievalChannel.VECTOR,
        raw_score=0.9,
        qualified_name="pkg.m.f",
        kind="fn",
    )
    assert c.channel == RetrievalChannel.VECTOR
    assert c.qualified_name == "pkg.m.f"


def test_ranked_result_channels_sorted_unique() -> None:
    r = RankedResult(
        unit_id="u1",
        final_score=0.5,
        breakdown=RankingFeatures(),
        channels=[RetrievalChannel.VECTOR, RetrievalChannel.GRAPH, RetrievalChannel.VECTOR],
    )
    assert r.channels == [RetrievalChannel.GRAPH, RetrievalChannel.VECTOR]


def test_context_packet_default_shape() -> None:
    p = ContextPacket(task="hello")
    assert p.task == "hello"
    assert p.context == []
    assert p.risks == []
    assert p.constraints == []
    assert p.changes == []
    assert p.confidence == 0.0


def test_context_entry_type_enum() -> None:
    e = ContextEntry(id="u1", type="constraint", score=0.7, data={"q": "x"})
    assert e.type == "constraint"
    with pytest.raises(ValidationError):
        ContextEntry(id="u1", type="not-a-real-type", score=0.5)
