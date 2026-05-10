from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.embeddings import DeterministicEmbedder
from core.retrieval import (
    GraphRetriever,
    HybridRetriever,
    MetadataRetriever,
    QueryPlanner,
    VectorRetriever,
)
from schemas import GraphNode, NodeKind, Query, RetrievalChannel


# =========================================================================
#                              GraphRetriever
# =========================================================================
def _gnode(node_id: str, kind: NodeKind = NodeKind.FUNCTION,
           qname: str | None = None) -> GraphNode:
    return GraphNode(
        node_id=node_id,
        kind=kind,
        repo_id="r",
        qualified_name=qname or node_id,
        name=node_id.rsplit(".", 1)[-1],
        file_path="f.py" if kind != NodeKind.EXTERNAL else None,
    )


@pytest.mark.asyncio
async def test_graph_retriever_bfs_respects_max_depth() -> None:
    # Linear chain seed -> a -> b -> c (each one neighbor away).
    graph: dict[str, list[GraphNode]] = {
        "seed": [_gnode("a")],
        "a": [_gnode("b")],
        "b": [_gnode("c")],
        "c": [],
    }
    source = AsyncMock()
    source.neighbors = AsyncMock(side_effect=lambda nid, **_: graph.get(nid, []))

    retriever = GraphRetriever(source, max_depth=2)
    cands = await retriever.search(["seed"])

    ids = [c.unit_id for c in cands]
    # depth 0 (seed) + depth 1 (a) + depth 2 (b); 'c' is beyond max_depth.
    assert "seed" in ids and "a" in ids and "b" in ids
    assert "c" not in ids
    # Determinism: hits sorted by (depth, node_id).
    assert ids == sorted(ids, key=lambda x: ({"seed": 0, "a": 1, "b": 2}[x], x))


@pytest.mark.asyncio
async def test_graph_retriever_skips_external_nodes() -> None:
    source = AsyncMock()
    source.neighbors = AsyncMock(return_value=[
        _gnode("internal", kind=NodeKind.FUNCTION),
        _gnode("ext", kind=NodeKind.EXTERNAL, qname="numpy"),
    ])
    retriever = GraphRetriever(source, max_depth=2)
    cands = await retriever.search(["seed"])
    ids = {c.unit_id for c in cands}
    assert "ext" not in ids
    assert "internal" in ids
    assert "seed" in ids


@pytest.mark.asyncio
async def test_graph_retriever_proximity_decreases_with_depth() -> None:
    source = AsyncMock()
    source.neighbors = AsyncMock(side_effect=lambda nid, **_: (
        [_gnode("a")] if nid == "seed"
        else [_gnode("b")] if nid == "a"
        else []
    ))
    cands = await GraphRetriever(source, max_depth=3).search(["seed"])
    by_id = {c.unit_id: c for c in cands}
    assert by_id["seed"].raw_score == 1.0
    assert by_id["a"].raw_score < by_id["seed"].raw_score
    assert by_id["b"].raw_score < by_id["a"].raw_score
    assert by_id["a"].extra["depth"] == 1
    assert by_id["b"].extra["depth"] == 2


@pytest.mark.asyncio
async def test_graph_retriever_isolates_neighbor_failures() -> None:
    source = AsyncMock()
    source.neighbors = AsyncMock(side_effect=RuntimeError("neo4j down"))
    cands = await GraphRetriever(source, max_depth=2).search(["seed"])
    # Seed itself still recorded; expansion failed silently.
    assert [c.unit_id for c in cands] == ["seed"]


@pytest.mark.asyncio
async def test_graph_retriever_is_deterministic_for_unsorted_seeds() -> None:
    source = AsyncMock()
    source.neighbors = AsyncMock(return_value=[])
    a = await GraphRetriever(source, max_depth=1).search(["b", "a", "a"])
    b = await GraphRetriever(source, max_depth=1).search(["a", "b"])
    assert [c.unit_id for c in a] == [c.unit_id for c in b]


# =========================================================================
#                              VectorRetriever
# =========================================================================
def _qhit(point_id: str, score: float, payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(id=point_id, score=score, payload=payload)


@pytest.mark.asyncio
async def test_vector_retriever_top_k_with_payload_filter() -> None:
    client = AsyncMock()
    client.search = AsyncMock(return_value=[
        _qhit("u1", 0.9, {"kind": "fn", "qualified_name": "p.f",
                          "file_path": "p.py", "has_vector": True}),
        _qhit("u2", 0.5, {"kind": "cls", "qualified_name": "p.C",
                          "file_path": "p.py", "has_vector": True}),
    ])
    retriever = VectorRetriever(
        client=client, embedder=DeterministicEmbedder(dimension=8),
        collection="repo_r",
    )
    cands = await retriever.search("auth", top_k=10, unit_kinds=["fn"])
    # `cls` filtered out by unit_kinds.
    assert [c.unit_id for c in cands] == ["u1"]
    assert cands[0].channel == RetrievalChannel.VECTOR


@pytest.mark.asyncio
async def test_vector_retriever_skips_payload_only_phase2_points() -> None:
    """Points with `has_vector=False` (Phase-2 placeholder writes) must
    NOT bubble up — they have no real embedding to compare against."""
    client = AsyncMock()
    client.search = AsyncMock(return_value=[
        _qhit("u1", 0.9, {"kind": "fn", "has_vector": False}),
        _qhit("u2", 0.7, {"kind": "fn", "has_vector": True}),
    ])
    retriever = VectorRetriever(
        client=client, embedder=DeterministicEmbedder(dimension=8),
        collection="repo_r",
    )
    cands = await retriever.search("x", top_k=5)
    assert [c.unit_id for c in cands] == ["u2"]


@pytest.mark.asyncio
async def test_vector_retriever_clips_negative_scores() -> None:
    client = AsyncMock()
    client.search = AsyncMock(return_value=[
        _qhit("u1", -0.4, {"has_vector": True}),
        _qhit("u2", 1.5, {"has_vector": True}),
    ])
    cands = await VectorRetriever(
        client=client, embedder=DeterministicEmbedder(dimension=8),
        collection="c",
    ).search("x", top_k=5)
    assert all(0.0 <= c.raw_score <= 1.0 for c in cands)


@pytest.mark.asyncio
async def test_vector_retriever_returns_empty_on_backend_failure() -> None:
    client = AsyncMock()
    client.search = AsyncMock(side_effect=RuntimeError("qdrant down"))
    cands = await VectorRetriever(
        client=client, embedder=DeterministicEmbedder(dimension=8),
        collection="c",
    ).search("x", top_k=5)
    assert cands == []


# =========================================================================
#                            MetadataRetriever
# =========================================================================
class _Row:
    def __init__(self, **fields: Any) -> None:
        self._mapping = fields

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)


class _Result:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def all(self) -> list[_Row]:
        return self._rows


class _Conn:
    def __init__(self, rows: list[_Row] | Exception) -> None:
        self._rows = rows
        self.last_params: dict[str, Any] | None = None

    async def execute(self, _stmt: Any, params: dict[str, Any]) -> _Result:
        self.last_params = params
        if isinstance(self._rows, Exception):
            raise self._rows
        return _Result(self._rows)


class _Engine:
    def __init__(self, rows: list[_Row] | Exception) -> None:
        self.conn = _Conn(rows)

    @asynccontextmanager
    async def connect(self):
        yield self.conn


@pytest.mark.asyncio
async def test_metadata_retriever_returns_candidates_with_metadata() -> None:
    rows = [
        _Row(
            unit_id="u1", repo_id="r", qualified_name="pkg.m.f", kind="fn",
            file_path="pkg/m.py", line_start=1, line_end=5,
            source_sha="s", updated_at=None,
        ),
    ]
    engine = _Engine(rows)
    cands = await MetadataRetriever(engine).search("auth", repo_id="r", top_k=10)
    assert [c.unit_id for c in cands] == ["u1"]
    assert cands[0].channel == RetrievalChannel.METADATA
    # Filter parameters carry through to SQL.
    assert engine.conn.last_params["pattern"] == "%auth%"
    assert engine.conn.last_params["no_kind_filter"] is True


@pytest.mark.asyncio
async def test_metadata_retriever_handles_db_failure_gracefully() -> None:
    engine = _Engine(RuntimeError("postgres down"))
    cands = await MetadataRetriever(engine).search("x", repo_id="r", top_k=10)
    assert cands == []


# =========================================================================
#                              HybridRetriever
# =========================================================================
@pytest.mark.asyncio
async def test_hybrid_runs_all_enabled_channels_in_parallel() -> None:
    vector = AsyncMock()
    vector.search = AsyncMock(return_value=[
        SimpleNamespace(unit_id="u1", channel=RetrievalChannel.VECTOR,
                        raw_score=0.9, file_path=None, qualified_name=None,
                        kind=None, extra={}),
    ])
    graph = AsyncMock()
    graph.search = AsyncMock(return_value=[])
    metadata = AsyncMock()
    metadata.search = AsyncMock(return_value=[
        SimpleNamespace(unit_id="u2", channel=RetrievalChannel.METADATA,
                        raw_score=0.5, file_path=None, qualified_name=None,
                        kind=None, extra={}),
    ])

    # Wrap mocks in real classes the HybridRetriever expects, by
    # constructing minimal fakes that satisfy duck typing.
    class _F:
        def __init__(self, m): self._m = m
        async def search(self, *a, **kw): return await self._m.search(*a, **kw)

    hybrid = HybridRetriever(
        planner=QueryPlanner(default_max_depth=2),
        graph=_F(graph),
        vector=_F(vector),
        metadata=_F(metadata),
    )
    res = await hybrid.run(
        Query(text="auth", repo_id="r", top_k=5, seed_unit_ids=["seed"]),
        query_id="q",
    )
    assert res.vector_hits == 1
    assert res.metadata_hits == 1
    assert "u1" in {c.unit_id for c in res.candidates}
    assert "u2" in {c.unit_id for c in res.candidates}


@pytest.mark.asyncio
async def test_hybrid_does_not_invoke_graph_without_seeds() -> None:
    graph = AsyncMock()
    graph.search = AsyncMock(return_value=[])
    vector = AsyncMock()
    vector.search = AsyncMock(return_value=[])
    metadata = AsyncMock()
    metadata.search = AsyncMock(return_value=[])

    class _F:
        def __init__(self, m): self._m = m
        async def search(self, *a, **kw): return await self._m.search(*a, **kw)

    hybrid = HybridRetriever(
        planner=QueryPlanner(),
        graph=_F(graph), vector=_F(vector), metadata=_F(metadata),
    )
    await hybrid.run(Query(text="x", repo_id="r"), query_id="q")
    graph.search.assert_not_awaited()
    vector.search.assert_awaited()
    metadata.search.assert_awaited()


@pytest.mark.asyncio
async def test_hybrid_isolates_channel_failure() -> None:
    class _Fail:
        async def search(self, *_a, **_kw):
            raise RuntimeError("backend down")

    class _Ok:
        async def search(self, *_a, **_kw):
            return [SimpleNamespace(unit_id="u1", channel=RetrievalChannel.METADATA,
                                    raw_score=0.5, file_path=None,
                                    qualified_name=None, kind=None, extra={})]

    hybrid = HybridRetriever(
        planner=QueryPlanner(),
        graph=None,
        vector=_Fail(),
        metadata=_Ok(),
    )
    res = await hybrid.run(Query(text="x", repo_id="r"), query_id="q")
    assert "vector" in res.failed_channels
    assert res.metadata_hits == 1
