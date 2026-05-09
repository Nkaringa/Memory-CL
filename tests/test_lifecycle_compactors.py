from __future__ import annotations

import textwrap

import pytest

from core.ingestion import GraphBuilder
from core.lifecycle import (
    EmbeddingRefreshScheduler,
    GraphCompactor,
    MemoryCompactor,
    RefreshReason,
    RelevanceBreakdown,
)
from core.parsing import PythonParser

REPO = "r"
COMMIT = "c"


def _units(source: str, file_path: str = "pkg/m.py") -> list:
    return PythonParser().parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO, file_path=file_path, commit_sha=COMMIT,
    )


def _bd(eid: str, score: float, *, centrality: float = 0.0) -> RelevanceBreakdown:
    return RelevanceBreakdown(
        entity_id=eid, score=score, usage=0.0, recency=0.0,
        centrality=centrality, success=0.0,
    )


# ---- MemoryCompactor -------------------------------------------------------
def test_memory_compactor_skips_modules_with_no_low_score_units() -> None:
    units = _units("def f(): pass\nclass C: pass\n")
    scores = {u.unit_id: _bd(u.unit_id, 0.9) for u in units}
    plan = MemoryCompactor(low_priority_threshold=0.3).plan(
        units=units, scores=scores,
    )
    assert plan.entries == ()


def test_memory_compactor_compacts_low_score_functions_into_module_summary() -> None:
    units = _units("def alpha(): pass\ndef beta(): pass\nclass C: pass\n")
    # alpha is low-score → compaction candidate; beta + C survive.
    scores = {u.unit_id: _bd(u.unit_id, 0.05 if u.name == "alpha" else 0.9)
              for u in units}
    plan = MemoryCompactor(low_priority_threshold=0.3).plan(
        units=units, scores=scores,
    )
    assert plan.merged_count == 1
    [entry] = plan.entries
    assert entry.module_qname == "pkg.m"
    # The summary still lists the surviving function (beta) and class.
    assert "beta" in entry.summary.fn
    assert "C" in entry.summary.cls


def test_memory_compactor_is_deterministic() -> None:
    units = _units("def alpha(): pass\ndef beta(): pass\n")
    scores = {u.unit_id: _bd(u.unit_id, 0.05 if "alpha" in u.qualified_name else 0.9)
              for u in units}
    a = MemoryCompactor(low_priority_threshold=0.3).plan(units=units, scores=scores)
    b = MemoryCompactor(low_priority_threshold=0.3).plan(units=units, scores=scores)
    assert [e.merged_unit_ids for e in a.entries] == \
           [e.merged_unit_ids for e in b.entries]


def test_memory_compactor_rejects_invalid_threshold() -> None:
    with pytest.raises(ValueError):
        MemoryCompactor(low_priority_threshold=1.5)


# ---- GraphCompactor --------------------------------------------------------
def test_graph_compactor_merges_low_centrality_leaves_into_module() -> None:
    units = _units("def a(): pass\ndef b(): pass\n")
    res = GraphBuilder().build(units)
    # All function nodes have low centrality (no incoming edges).
    scores = {n.node_id: _bd(n.node_id, 0.1, centrality=0.0) for n in res.nodes}
    plan = GraphCompactor(centrality_threshold=0.2).plan(
        nodes=res.nodes, edges=res.edges, scores=scores,
    )
    # Exactly one merge target — the module — and the function nodes folded.
    assert len(plan.merges) == 1
    assert plan.merges[0].target_kind.value == "Module"
    assert plan.merged_count == 2


def test_graph_compactor_preserves_edges_via_rewrite() -> None:
    units = _units("def helper(): pass\ndef caller(): helper()\n")
    res = GraphBuilder().build(units)
    scores = {n.node_id: _bd(n.node_id, 0.1, centrality=0.0) for n in res.nodes}
    plan = GraphCompactor(centrality_threshold=0.2).plan(
        nodes=res.nodes, edges=res.edges, scores=scores,
    )
    [merge] = plan.merges
    # Edges that touched the victims survive — rewritten to terminate
    # at the module aggregate. Self-edges are dropped.
    assert all(src != dst for (src, _kind, dst) in merge.preserved_edge_ids)


def test_graph_compactor_skips_high_centrality_nodes() -> None:
    units = _units("def a(): pass\n")
    res = GraphBuilder().build(units)
    scores = {n.node_id: _bd(n.node_id, 0.1, centrality=0.9) for n in res.nodes}
    plan = GraphCompactor(centrality_threshold=0.2).plan(
        nodes=res.nodes, edges=res.edges, scores=scores,
    )
    assert plan.merges == ()


# ---- EmbeddingRefreshScheduler --------------------------------------------
def test_refresh_triggered_by_low_relevance() -> None:
    scores = [_bd("u1", 0.1)]
    plan = EmbeddingRefreshScheduler(refresh_threshold=0.4).plan(
        scores=scores,
        previous_signatures={"u1": "x"},
        current_signatures={"u1": "x"},
    )
    assert [d.entity_id for d in plan.decisions] == ["u1"]
    assert RefreshReason.LOW_RELEVANCE in plan.decisions[0].reasons


def test_refresh_triggered_by_neighbor_drift() -> None:
    scores = [_bd("u1", 0.9)]
    plan = EmbeddingRefreshScheduler(refresh_threshold=0.4).plan(
        scores=scores,
        previous_signatures={"u1": "old"},
        current_signatures={"u1": "new"},
    )
    assert RefreshReason.NEIGHBOR_DRIFT in plan.decisions[0].reasons


def test_refresh_triggered_by_low_success_rate() -> None:
    scores = [
        RelevanceBreakdown(
            entity_id="u1", score=0.9,
            usage=0.0, recency=0.0, centrality=0.0,
            success=0.3,  # < success_floor
        ),
    ]
    plan = EmbeddingRefreshScheduler(refresh_threshold=0.4).plan(
        scores=scores,
        previous_signatures={}, current_signatures={},
    )
    assert RefreshReason.LOW_SUCCESS_RATE in plan.decisions[0].reasons


def test_refresh_no_op_when_all_signals_healthy() -> None:
    scores = [
        RelevanceBreakdown(
            entity_id="u1", score=0.9,
            usage=0.0, recency=0.0, centrality=0.0,
            success=0.9,
        ),
    ]
    plan = EmbeddingRefreshScheduler(refresh_threshold=0.4).plan(
        scores=scores,
        previous_signatures={"u1": "x"}, current_signatures={"u1": "x"},
    )
    assert plan.decisions == ()


def test_refresh_plan_is_deterministic() -> None:
    scores = [_bd("z", 0.1), _bd("a", 0.1), _bd("m", 0.1)]
    a = EmbeddingRefreshScheduler(refresh_threshold=0.4).plan(
        scores=scores, previous_signatures={}, current_signatures={},
    )
    b = EmbeddingRefreshScheduler(refresh_threshold=0.4).plan(
        scores=list(reversed(scores)), previous_signatures={}, current_signatures={},
    )
    assert a.to_refresh == b.to_refresh
