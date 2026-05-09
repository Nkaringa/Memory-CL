from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock

import pytest

from core.ingestion import GraphBuilder
from core.integrity import (
    ChecksumVerifier,
    DriftSeverity,
    EmbeddingDriftDetector,
    GraphValidator,
    Quarantine,
    SchemaValidator,
)
from core.parsing import PythonParser


def _units(source: str, file_path: str = "pkg/m.py") -> list:
    return PythonParser().parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id="r", file_path=file_path, commit_sha="c",
    )


# =========================================================================
#                              ChecksumVerifier
# =========================================================================
def test_checksum_verifier_passes_clean_units() -> None:
    units = _units("def f(): pass\n")
    report = ChecksumVerifier().verify_units(units)
    assert report.has_violations is False
    assert report.matched == report.total


def test_checksum_verifier_flags_corrupted_unit() -> None:
    units = _units("def f(): pass\n")
    [tampered] = [u.model_copy(update={"content": "def f(): return 'PWNED'\n"})
                  for u in units if u.kind.value == "fn"]
    report = ChecksumVerifier().verify_units([tampered])
    assert report.has_violations
    assert tampered.unit_id in report.mismatched_ids


@pytest.mark.asyncio
async def test_quarantine_marks_then_clears() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="checksum_mismatch")
    redis.delete = AsyncMock(return_value=1)
    q = Quarantine(redis)
    await q.mark(repo_id="r", entity_id="u1", reason="checksum_mismatch")
    assert await q.is_quarantined(repo_id="r", entity_id="u1")
    redis.set.assert_awaited()
    await q.clear(repo_id="r", entity_id="u1")
    redis.delete.assert_awaited()


@pytest.mark.asyncio
async def test_verifier_quarantines_mismatches_via_redis() -> None:
    units = _units("def f(): pass\n")
    [bad] = [u.model_copy(update={"content": "broken"})
             for u in units if u.kind.value == "fn"]
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="")
    q = Quarantine(redis)
    report = await ChecksumVerifier().quarantine_mismatches(
        repo_id="r", units=[bad], quarantine=q,
    )
    assert bad.unit_id in report.quarantined_ids
    redis.set.assert_awaited()


# =========================================================================
#                              GraphValidator
# =========================================================================
def test_graph_validator_passes_clean_graph() -> None:
    units = _units("def f(): pass\nclass C: pass\n")
    res = GraphBuilder().build(units)
    units_by_id = {u.unit_id: u for u in units}
    report = GraphValidator().validate(
        nodes=res.nodes, edges=res.edges, units_by_id=units_by_id,
    )
    assert report.ok


def test_graph_validator_flags_orphan_edges() -> None:
    """Manually inject an edge whose dst_id doesn't exist in the node set."""
    from schemas import EdgeKind, GraphEdge, GraphNode, NodeKind
    nodes = [
        GraphNode(node_id="src", kind=NodeKind.FUNCTION, repo_id="r",
                  qualified_name="src", name="src", file_path="f.py",
                  line_start=1, line_end=2, commit_sha="c", source_sha="s"),
    ]
    edges = [
        GraphEdge(src_id="src", kind=EdgeKind.CALLS, dst_id="ghost",
                  repo_id="r", commit_sha="c"),
    ]
    report = GraphValidator().validate(nodes=nodes, edges=edges)
    assert not report.ok
    assert any(v.kind == "orphan_edge" for v in report.violations)


def test_graph_validator_detects_id_mismatch_against_units() -> None:
    """When unit_id doesn't equal node_id, report id_mismatch."""
    units = _units("def f(): pass\n")
    res = GraphBuilder().build(units)
    fn_unit = next(u for u in units if u.kind.value == "fn")
    # Forge a units_by_id where the lookup gives back a different unit_id.
    fake = fn_unit.model_copy(update={"unit_id": "different-id"})
    report = GraphValidator().validate(
        nodes=res.nodes, edges=res.edges,
        units_by_id={fn_unit.unit_id: fake},
    )
    assert any(v.kind == "id_mismatch" for v in report.violations)


# =========================================================================
#                              SchemaValidator
# =========================================================================
def test_schema_validator_passes_current_schema() -> None:
    units = _units("def f(): pass\n")
    report = SchemaValidator().validate(units)
    assert report.ok


def test_schema_validator_flags_old_versions() -> None:
    units = _units("def f(): pass\n")
    [stale] = [u.model_copy(update={"schema_version": "0"}) for u in units[:1]]
    report = SchemaValidator().validate([stale])
    assert not report.ok
    assert "0" in report.incompatible_versions


# =========================================================================
#                          EmbeddingDriftDetector
# =========================================================================
def test_drift_low_for_identical_vectors() -> None:
    vec = [0.1, 0.2, 0.3, 0.4]
    detector = EmbeddingDriftDetector()
    report = detector.analyze_embeddings(
        baseline={"u1": vec}, current={"u1": vec},
    )
    assert report.severity == DriftSeverity.LOW


def test_drift_critical_for_inverted_vector() -> None:
    detector = EmbeddingDriftDetector()
    a = [1.0, 0.0, 0.0]
    b = [-1.0, 0.0, 0.0]
    report = detector.analyze_embeddings(
        baseline={"u1": a}, current={"u1": b},
    )
    assert report.severity == DriftSeverity.CRITICAL


def test_ranking_drift_for_disjoint_top_k() -> None:
    detector = EmbeddingDriftDetector()
    report = detector.analyze_ranking(
        baseline_top_k=["a", "b", "c"],
        current_top_k=["x", "y", "z"],
    )
    assert report.severity in {DriftSeverity.HIGH, DriftSeverity.CRITICAL}


def test_graph_drift_zero_for_identical_shards() -> None:
    detector = EmbeddingDriftDetector()
    edges = [("u1", "CALLS", "u2"), ("u2", "CALLS", "u3")]
    report = detector.analyze_graph_edges(
        edges_per_shard={"s1": edges, "s2": edges},
    )
    assert report.severity == DriftSeverity.LOW
    assert report.summary["divergence_ratio"] == 0.0


def test_drift_report_contains_suggested_action() -> None:
    detector = EmbeddingDriftDetector()
    report = detector.analyze_embeddings(
        baseline={"u": [1.0, 0.0]},
        current={"u": [-1.0, 0.0]},
    )
    assert "ESCALATE" in report.suggested_action or "re-index" in report.suggested_action
