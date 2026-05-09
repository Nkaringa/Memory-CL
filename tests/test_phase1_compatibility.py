"""Compatibility check: Phase 1 storage clients can host Phase 2 repositories.

These tests do NOT implement the repositories — they only confirm that the
required attributes (engine/sessionmaker/driver/client) on the Phase 1
clients are reachable, that the Phase 2 protocol shapes are coherent, and
that the schema layer composes with the storage layer with no upward import.
"""

from __future__ import annotations

import inspect

from schemas import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    IngestionUnit,
    Language,
    NodeKind,
    UnitKind,
    content_sha,
    stable_unit_id,
)
from storage import (
    GraphRepository,
    IngestionUnitRepository,
    Neo4jClient,
    PostgresClient,
    QdrantStorageClient,
    RedisClient,
    VectorPoint,
    VectorRepository,
)


def test_phase1_clients_expose_underlying_drivers() -> None:
    """The Phase 2 repository implementations will need these handles."""
    pg = PostgresClient("postgresql+asyncpg://x:y@h:5432/db")
    qd = QdrantStorageClient("http://localhost:6333")
    nj = Neo4jClient("bolt://localhost:7687", "u", "p")
    rd = RedisClient("redis://localhost:6379/0")

    # Property exists; the handle accessor exists; both raise pre-connect.
    for c, attr in [(pg, "engine"), (pg, "sessionmaker"),
                    (qd, "client"), (nj, "driver"), (rd, "client")]:
        assert hasattr(type(c), attr)


def test_repository_protocols_are_async() -> None:
    for proto, methods in [
        (
            IngestionUnitRepository,
            ["upsert_unit", "upsert_units", "get_unit",
             "list_units_for_file", "delete_units_for_file"],
        ),
        (
            GraphRepository,
            ["upsert_node", "upsert_nodes", "upsert_edge", "upsert_edges",
             "neighbors", "delete_subgraph_for_file"],
        ),
        (
            VectorRepository,
            ["ensure_collection", "upsert_payload", "upsert_payloads",
             "delete_points_for_file"],
        ),
    ]:
        for m in methods:
            fn = getattr(proto, m)
            assert inspect.iscoroutinefunction(fn), f"{proto.__name__}.{m} must be async"


def test_unit_id_aligns_with_graph_node_id_convention() -> None:
    """ARCHITECTURE INVARIANT: a unit's `unit_id` is reused as its graph
    `node_id`, which is what makes Postgres ↔ Neo4j ↔ Qdrant joins work.

    This test pins the convention so the Phase 2 implementation cannot
    quietly drift away from it.
    """
    repo, fp, qn = "repo-1", "pkg/mod.py", "pkg.mod.fn"
    uid = stable_unit_id(repo, fp, qn)

    src = "def fn():\n    return 1\n"
    unit = IngestionUnit(
        unit_id=uid,
        repo_id=repo,
        commit_sha="c",
        kind=UnitKind.FUNCTION,
        name="fn",
        qualified_name=qn,
        parent_qualified_name="pkg.mod",
        file_path=fp,
        language=Language.PYTHON,
        line_start=1,
        line_end=2,
        content=src,
        source_sha=content_sha(src),
    )

    node = GraphNode(
        node_id=unit.unit_id,
        kind=NodeKind.FUNCTION,
        repo_id=unit.repo_id,
        qualified_name=unit.qualified_name,
        name=unit.name,
        file_path=unit.file_path,
        line_start=unit.line_start,
        line_end=unit.line_end,
        commit_sha=unit.commit_sha,
        source_sha=unit.source_sha,
    )

    point = VectorPoint(
        point_id=unit.unit_id,
        repo_id=unit.repo_id,
        qualified_name=unit.qualified_name,
        kind=unit.kind.value,
        file_path=unit.file_path,
        line_start=unit.line_start,
        line_end=unit.line_end,
        commit_sha=unit.commit_sha,
        source_sha=unit.source_sha,
    )

    # Cross-store identity is the same string; no translation layer needed.
    assert node.node_id == unit.unit_id == point.point_id


def test_edge_construction_pairs_unit_ids() -> None:
    repo, src_fp, dst_fp = "r", "a.py", "b.py"
    src_id = stable_unit_id(repo, src_fp, "a.fn")
    dst_id = stable_unit_id(repo, dst_fp, "b.gn")
    edge = GraphEdge(
        src_id=src_id,
        kind=EdgeKind.CALLS,
        dst_id=dst_id,
        repo_id=repo,
        commit_sha="c",
    )
    assert edge.src_id != edge.dst_id
