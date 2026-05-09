from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from core.ingestion import GraphBuilder
from core.parsing import PythonParser
from core.reproducibility import (
    ReplayEngine,
    SystemSnapshotBuilder,
    VersionTokenStore,
)


def _ingest_repo(src: str = "def f(): pass\n"):
    units = PythonParser().parse_file(
        source=src, repo_id="r", file_path="m.py", commit_sha="c",
    )
    return units, GraphBuilder().build(units)


# ---- VersionTokenStore ----------------------------------------------------
@pytest.mark.asyncio
async def test_version_token_starts_at_v0() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    store = VersionTokenStore(redis)
    v = await store.current(tenant_id="t1")
    assert v.version == "v0"
    assert v.counter == 0


@pytest.mark.asyncio
async def test_version_token_advances_monotonically() -> None:
    redis = AsyncMock()
    counters = {"v": 0}

    async def fake_incr(_key):
        counters["v"] += 1
        return counters["v"]

    redis.incr = AsyncMock(side_effect=fake_incr)
    store = VersionTokenStore(redis)
    a = await store.advance(tenant_id="t1")
    b = await store.advance(tenant_id="t1")
    assert (a.counter, b.counter) == (1, 2)
    assert (a.version, b.version) == ("v1", "v2")


# ---- SystemSnapshotBuilder ------------------------------------------------
def test_snapshot_id_is_deterministic_for_same_state() -> None:
    _units, graph = _ingest_repo()
    builder = SystemSnapshotBuilder()

    def _build():
        return builder.build(
            tenant_id="acme",
            nodes=graph.nodes, edges=graph.edges,
            embeddings={"u1": [0.1, 0.2, 0.3]},
            retrieval_config={"semantic": 0.35, "graph": 0.25},
            mcp_tool_names=["get_context", "query_graph"],
            mcp_request_schemas={"get_context": "GetContextRequest"},
            state_version_token="v0",
            captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    a = _build()
    b = _build()
    assert a.snapshot_id == b.snapshot_id


def test_snapshot_id_differs_when_state_changes() -> None:
    _units, graph = _ingest_repo()
    builder = SystemSnapshotBuilder()

    base = builder.build(
        tenant_id="acme",
        nodes=graph.nodes, edges=graph.edges,
        embeddings={"u1": [0.1, 0.2]},
        retrieval_config={"semantic": 0.35},
        mcp_tool_names=["a"],
        mcp_request_schemas={"a": "S"},
        state_version_token="v0",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    drifted = builder.build(
        tenant_id="acme",
        nodes=graph.nodes, edges=graph.edges,
        embeddings={"u1": [0.9, 0.9]},  # ← changed
        retrieval_config={"semantic": 0.35},
        mcp_tool_names=["a"],
        mcp_request_schemas={"a": "S"},
        state_version_token="v0",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert base.snapshot_id != drifted.snapshot_id


def test_snapshot_components_cover_every_state_axis() -> None:
    _units, graph = _ingest_repo()
    snap = SystemSnapshotBuilder().build(
        tenant_id="acme",
        nodes=graph.nodes, edges=graph.edges,
        embeddings={},
        retrieval_config={"semantic": 0.35},
        mcp_tool_names=["a"], mcp_request_schemas={"a": "X"},
        state_version_token="v3",
    )
    payload = snap.to_payload()["components"]
    assert set(payload) == {
        "graph_state_hash",
        "embedding_index_hash",
        "retrieval_config_hash",
        "schema_version",
        "mcp_registry_hash",
        "state_version_token",
    }


# ---- ReplayEngine ---------------------------------------------------------
@pytest.mark.asyncio
async def test_replay_engine_reports_match_for_deterministic_op() -> None:
    _units, graph = _ingest_repo()
    snapshot = SystemSnapshotBuilder().build(
        tenant_id="acme",
        nodes=graph.nodes, edges=graph.edges,
        embeddings={}, retrieval_config={"semantic": 0.35},
        mcp_tool_names=[], mcp_request_schemas={},
        state_version_token="v0",
    )
    engine = ReplayEngine()

    async def deterministic_op() -> dict:
        return {"result": [1, 2, 3]}

    res = await engine.replay(
        snapshot, deterministic_op,
        expected_output={"result": [1, 2, 3]},
    )
    assert res.matches is True
    assert res.expected_hash == res.actual_hash


@pytest.mark.asyncio
async def test_replay_engine_detects_mismatch() -> None:
    _units, graph = _ingest_repo()
    snapshot = SystemSnapshotBuilder().build(
        tenant_id="acme",
        nodes=graph.nodes, edges=graph.edges,
        embeddings={}, retrieval_config={"semantic": 0.35},
        mcp_tool_names=[], mcp_request_schemas={},
        state_version_token="v0",
    )
    engine = ReplayEngine()

    async def shifted_op() -> dict:
        return {"result": "DIFFERENT"}

    res = await engine.replay(
        snapshot, shifted_op,
        expected_output={"result": [1, 2, 3]},
    )
    assert res.matches is False
    assert res.expected_hash != res.actual_hash


def test_replay_engine_equivalent_hashes_helper() -> None:
    engine = ReplayEngine()
    assert engine.equivalent_hashes({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert not engine.equivalent_hashes({"a": 1}, {"a": 2})
