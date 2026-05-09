from __future__ import annotations

import pytest

from core.diagnostics import (
    AnomalyDetector,
    AnomalySeverity,
    ConsistencyReporter,
    CorruptionDetector,
)
from core.integrity import (
    ChecksumReport,
    GraphIntegrityReport,
    IntegrityViolation,
    SchemaCompatibility,
)
from infra.audit.immutable_log_store import ImmutableLogStore


# ---- AnomalyDetector ------------------------------------------------------
def test_anomaly_detector_normal_for_uniform_samples() -> None:
    out = AnomalyDetector().analyze([10.0, 10.0, 10.0, 10.0])
    assert out.severity == AnomalySeverity.NORMAL


def test_anomaly_detector_flags_out_of_band_outlier() -> None:
    samples = [10.0] * 30 + [500.0]  # one extreme outlier
    out = AnomalyDetector(z_threshold=3.0).analyze(samples)
    assert out.severity == AnomalySeverity.ANOMALY
    assert 30 in out.outlier_indices


def test_anomaly_detector_watch_band_for_borderline_outliers() -> None:
    """A ~2.5-sigma outlier triggers WATCH (between 2-sigma and 3-sigma)."""
    # Base distribution with stdev ≈ 1.0 (alternating values around 10).
    base = [9.0, 11.0] * 25
    # Outlier ~2.5 stdev above the mean of 10.
    samples = [*base, 12.5]
    out = AnomalyDetector(z_threshold=3.0).analyze(samples)
    assert out.severity == AnomalySeverity.WATCH


def test_anomaly_detector_handles_empty_input() -> None:
    out = AnomalyDetector().analyze([])
    assert out.severity == AnomalySeverity.NORMAL
    assert out.sample_count == 0


def test_anomaly_detector_rejects_invalid_threshold() -> None:
    with pytest.raises(ValueError):
        AnomalyDetector(z_threshold=0)


# ---- ConsistencyReporter --------------------------------------------------
def test_consistency_reporter_full_overlap() -> None:
    out = ConsistencyReporter().report(
        postgres_ids=["a", "b"], neo4j_ids=["a", "b"], qdrant_ids=["a", "b"],
    )
    assert out.fully_consistent
    assert out.in_all_three == 2


def test_consistency_reporter_flags_postgres_only() -> None:
    out = ConsistencyReporter().report(
        postgres_ids=["a", "b", "c"],
        neo4j_ids=["a", "b"],
        qdrant_ids=["a", "b"],
    )
    assert not out.fully_consistent
    assert out.postgres_only == ("c",)


def test_consistency_reporter_three_way_diff() -> None:
    out = ConsistencyReporter().report(
        postgres_ids=["a", "b"],
        neo4j_ids=["b", "c"],
        qdrant_ids=["c", "d"],
    )
    assert out.postgres_only == ("a",)
    assert out.qdrant_only == ("d",)


# ---- CorruptionDetector ---------------------------------------------------
def _empty_checksum() -> ChecksumReport:
    return ChecksumReport(total=0, matched=0, mismatched_ids=())


def _empty_graph() -> GraphIntegrityReport:
    return GraphIntegrityReport(nodes_checked=0, edges_checked=0)


def _empty_schema() -> SchemaCompatibility:
    return SchemaCompatibility(expected_version="1", total_checked=0)


def test_corruption_detector_clean_when_no_signals() -> None:
    out = CorruptionDetector().detect(
        checksum=_empty_checksum(),
        graph=_empty_graph(),
        schema=_empty_schema(),
        audit_log=ImmutableLogStore(),
    )
    assert not out.has_corruption


def test_corruption_detector_aggregates_signals() -> None:
    cs = ChecksumReport(total=10, matched=8, mismatched_ids=("u1", "u2"))
    gv = GraphIntegrityReport(
        nodes_checked=2, edges_checked=2,
        violations=(IntegrityViolation(
            kind="orphan_edge", detail="x", src_id="u3", dst_id="ghost",
            edge_kind="CALLS",
        ),),
    )
    sv = SchemaCompatibility(expected_version="1", total_checked=5,
                             incompatible_ids=("u4",))
    report = CorruptionDetector().detect(
        checksum=cs, graph=gv, schema=sv,
    )
    assert report.has_corruption
    assert report.checksum_mismatches == 2
    assert report.graph_violations == 1
    assert report.schema_incompatibilities == 1
    assert {"u1", "u2", "u3", "ghost", "u4"}.issubset(set(report.affected_entity_ids))


def test_corruption_detector_flags_broken_audit_chain() -> None:
    store = ImmutableLogStore()
    store.append({"x": 1})
    # Tamper.
    from infra.audit.immutable_log_store import GENESIS_HASH, LogEntry
    store._entries[0] = LogEntry(  # type: ignore[attr-defined]
        seq=0, prev_hash=GENESIS_HASH,
        hash=store.get(0).hash, payload={"x": 999},
    )
    report = CorruptionDetector().detect(
        checksum=_empty_checksum(),
        graph=_empty_graph(),
        schema=_empty_schema(),
        audit_log=store,
    )
    assert not report.audit_chain_intact
    assert report.has_corruption
