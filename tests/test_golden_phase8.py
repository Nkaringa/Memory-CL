"""Phase-8 golden gate.

Wires every Phase-8 component end-to-end against the fixture repo
(via the Phase-2/3 build path) and exercises:

    * audit chain capture for every operation
    * tenant isolation through AccessControl
    * graph + checksum + schema integrity over real data
    * snapshot determinism across two runs
    * replay engine reports match for the deterministic path
    * corruption detector aggregates the integrity reports
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.diagnostics import (
    ConsistencyReporter,
    CorruptionDetector,
)
from core.governance import (
    AccessControl,
    AccessRequest,
    AuditAction,
    AuditActor,
    AuditLogger,
    PolicyEngine,
    Tenant,
    TenantManager,
    deny_external_retrieval,
)
from core.ingestion import GraphBuilder
from core.integrity import (
    ChecksumVerifier,
    DriftSeverity,
    EmbeddingDriftDetector,
    GraphValidator,
    SchemaValidator,
)
from core.parsing import FileWalker, PythonParser
from core.reproducibility import (
    ReplayEngine,
    SystemSnapshotBuilder,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


def _ingest_fixture() -> tuple[list, object]:
    walk = FileWalker().walk(FIXTURE, repo_id="acme")
    parser = PythonParser()
    units = []
    for ref in walk.files:
        units.extend(parser.parse_file(
            source=(FIXTURE / ref.path).read_text(encoding="utf-8"),
            repo_id="acme", file_path=ref.path, commit_sha="commit-deadbeef",
        ))
    return units, GraphBuilder().build(units)


def _build_tenants() -> TenantManager:
    tm = TenantManager()
    tm.register_tenant(Tenant(tenant_id="acme-corp", name="ACME"))
    tm.register_tenant(Tenant(tenant_id="other-co", name="OTHER"))
    tm.assign_repo(tenant_id="acme-corp", repo_id="acme")
    return tm


# ---- audit + governance integration --------------------------------------
def test_phase8_audit_chain_captures_every_governance_decision() -> None:
    audit = AuditLogger()
    tm = _build_tenants()
    eng = PolicyEngine([deny_external_retrieval()])
    ac = AccessControl(tenants=tm, policies=eng, audit=audit)

    # 1. legitimate retrieval — ALLOW
    decision = ac.check(AccessRequest(
        actor=AuditActor.AGENT, role="agent",
        tenant_id="acme-corp", repo_id="acme",
        action="retrieve", entity_id="u1", entity_kind="Function",
    ))
    assert decision.allowed

    # 2. cross-tenant attempt — DENY
    decision = ac.check(AccessRequest(
        actor=AuditActor.AGENT, role="agent",
        tenant_id="other-co", repo_id="acme",
        action="retrieve", entity_id="u1", entity_kind="Function",
    ))
    assert not decision.allowed

    # 3. external-target attempt — DENY by policy
    decision = ac.check(AccessRequest(
        actor=AuditActor.AGENT, role="agent",
        tenant_id="acme-corp", repo_id="acme",
        action="retrieve", entity_id="numpy", entity_kind="External",
    ))
    assert not decision.allowed

    # The audit chain captured all three decisions and remains intact.
    assert len(audit.store) == 3
    assert audit.verify() is True
    actions = [e.payload["action"] for e in audit.store]
    assert all(a == AuditAction.POLICY_DECIDE.value for a in actions)


# ---- integrity over real fixture data ------------------------------------
def test_phase8_integrity_passes_for_clean_fixture() -> None:
    units, graph = _ingest_fixture()
    units_by_id = {u.unit_id: u for u in units}

    cs = ChecksumVerifier().verify_units(units)
    gv = GraphValidator().validate(
        nodes=graph.nodes, edges=graph.edges, units_by_id=units_by_id,
    )
    sv = SchemaValidator().validate(units)
    corruption = CorruptionDetector().detect(
        checksum=cs, graph=gv, schema=sv,
    )

    assert not cs.has_violations
    assert gv.ok
    assert sv.ok
    assert not corruption.has_corruption


def test_phase8_integrity_flags_tampered_unit() -> None:
    units, _graph = _ingest_fixture()
    # Corrupt one unit's content; checksum must catch it.
    target = next(u for u in units if u.kind.value == "fn")
    tampered = target.model_copy(update={"content": "raise SystemExit('PWN')"})
    cs = ChecksumVerifier().verify_units([tampered])
    assert cs.has_violations
    assert tampered.unit_id in cs.mismatched_ids


# ---- snapshot + replay determinism ----------------------------------------
def test_phase8_snapshot_id_byte_deterministic_across_runs() -> None:
    units, graph = _ingest_fixture()
    builder = SystemSnapshotBuilder()

    def _build():
        return builder.build(
            tenant_id="acme-corp",
            nodes=graph.nodes, edges=graph.edges,
            embeddings={u.unit_id: [0.0, 1.0, 0.0] for u in units},
            retrieval_config={"semantic": 0.35, "graph": 0.25,
                              "recency": 0.20, "importance": 0.15,
                              "feedback": 0.05},
            mcp_tool_names=["get_context", "query_graph"],
            mcp_request_schemas={"get_context": "GetContextRequest"},
            state_version_token="v0",
            captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    a, b = _build(), _build()
    assert a.snapshot_id == b.snapshot_id


@pytest.mark.asyncio
async def test_phase8_replay_engine_verifies_deterministic_op() -> None:
    units, graph = _ingest_fixture()
    snapshot = SystemSnapshotBuilder().build(
        tenant_id="acme-corp",
        nodes=graph.nodes, edges=graph.edges,
        embeddings={}, retrieval_config={"semantic": 0.35},
        mcp_tool_names=[], mcp_request_schemas={},
        state_version_token="v0",
    )
    engine = ReplayEngine()

    # Use the actual graph builder as the deterministic operation.
    async def rebuild_graph():
        return sorted(n.node_id for n in GraphBuilder().build(units).nodes)

    res = await engine.replay(
        snapshot, rebuild_graph,
        expected_output=sorted(n.node_id for n in graph.nodes),
    )
    # Note: builder output is already deterministic, so the live result
    # should match the expected output.
    assert res.matches is True


# ---- consistency reporter -------------------------------------------------
def test_phase8_consistency_reporter_passes_for_aligned_stores() -> None:
    units, _graph = _ingest_fixture()
    ids = sorted(u.unit_id for u in units)
    out = ConsistencyReporter().report(
        postgres_ids=ids, neo4j_ids=ids, qdrant_ids=ids,
    )
    assert out.fully_consistent
    assert out.in_all_three == len(units)


def test_phase8_consistency_reporter_flags_orphaned_qdrant_points() -> None:
    units, _graph = _ingest_fixture()
    ids = [u.unit_id for u in units]
    # Pretend Qdrant has a leftover point that no longer exists in Postgres.
    out = ConsistencyReporter().report(
        postgres_ids=ids, neo4j_ids=ids, qdrant_ids=[*ids, "ghost-point"],
    )
    assert not out.fully_consistent
    assert "ghost-point" in out.qdrant_only


# ---- drift detection over real-style snapshots ----------------------------
def test_phase8_embedding_drift_low_for_unchanged_index() -> None:
    detector = EmbeddingDriftDetector()
    baseline = {"u1": [0.1, 0.9], "u2": [0.5, 0.5]}
    report = detector.analyze_embeddings(baseline=baseline, current=baseline)
    assert report.severity == DriftSeverity.LOW
    assert report.summary["max_shift"] == 0.0
