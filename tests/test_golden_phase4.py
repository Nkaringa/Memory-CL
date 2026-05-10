"""Phase-4 golden gate: Phase 2 + Phase 3 + Phase 4 over the fixture repo.

This is the acceptance gate for retrieval. We:

1. Walk + parse + build graph (Phase 2)
2. Run the compression pipeline to populate dense records + a fake
   "Qdrant" with real embeddings (Phase 3)
3. Run the retrieval pipeline twice, asserting byte-equal ContextPacket
   bytes across runs (Phase 4 determinism rule)
4. Verify the ContextPacket shape + ranking formula contract

The "Qdrant" used here is an in-memory fake that exposes the same
duck-typed `search()` method the real client offers, so this test
exercises the full retrieval orchestration without requiring docker.
"""

from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.compression import CompressionContext
from core.compression.pipeline import CompressionPipeline
from core.context import ContextAssembler
from core.context.context_assembler import AssemblyOptions
from core.embeddings import ChunkingStrategy, DeterministicEmbedder
from core.ingestion import GraphBuilder
from core.parsing import FileWalker, PythonParser
from core.ranking import RankingModel
from core.retrieval import (
    GraphRetriever,
    HybridRetriever,
    QueryPlanner,
    VectorRetriever,
)
from schemas import CompressionMetrics, Query

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


# ---- Fakes ----------------------------------------------------------------
class _FakeQdrant:
    """In-memory Qdrant stand-in.

    Records every upsert and replays the highest-cosine matches against
    a query vector. Deterministic by construction.
    """

    def __init__(self) -> None:
        self.points: dict[str, tuple[list[float], dict]] = {}

    def upsert_call(self, points):
        # Phase-3 hands us `VectorPoint` (storage DTO) — flatten the
        # fields the retriever payload-shape expects.
        for p in points:
            payload = {
                "kind": p.kind,
                "qualified_name": p.qualified_name,
                "file_path": p.file_path,
                "has_vector": p.vector is not None,
            }
            self.points[str(p.point_id)] = (list(p.vector or []), payload)

    async def search(self, *, collection_name: str, query_vector, limit: int,
                     query_filter=None, with_payload: bool = True):
        scored = []
        for pid, (vec, payload) in self.points.items():
            if not vec:
                continue
            cos = _cosine(query_vector, vec)
            scored.append(SimpleNamespace(id=pid, score=cos, payload=payload))
        scored.sort(key=lambda h: (-h.score, h.id))
        return scored[:limit]


def _cosine(a, b) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class _FakeGraphRepo:
    """In-memory adjacency, just enough for GraphRetriever.neighbors."""

    def __init__(self, nodes_by_id, adjacency):
        self._nodes = nodes_by_id
        self._adj = adjacency

    async def neighbors(self, node_id, edge_kinds=None, depth=1):
        out = []
        for n in self._adj.get(node_id, []):
            if n in self._nodes:
                out.append(self._nodes[n])
        return out


# ---- Fixture pipeline -----------------------------------------------------
async def _run_phase23() -> tuple[list, _FakeQdrant, _FakeGraphRepo]:
    walk = FileWalker().walk(FIXTURE_REPO, repo_id="acme")
    parser = PythonParser()
    units = []
    for ref in walk.files:
        units.extend(parser.parse_file(
            source=(FIXTURE_REPO / ref.path).read_text(encoding="utf-8"),
            repo_id="acme",
            file_path=ref.path,
            commit_sha="commit-deadbeef",
        ))
    graph = GraphBuilder().build(units)

    # Phase 3 — capture vector points into the fake Qdrant.
    fake_qdrant = _FakeQdrant()
    vector_repo = AsyncMock()
    vector_repo.ensure_collection = AsyncMock()
    vector_repo.upsert_payloads = AsyncMock(side_effect=lambda c, pts: (
        fake_qdrant.upsert_call(pts) or len(list(pts))
    ))
    ctx = CompressionContext(
        repo_id="acme", commit_sha="commit-deadbeef",
        units_collection="repo_acme",
        vector_repo=vector_repo, metrics=CompressionMetrics(),
    )
    await CompressionPipeline(
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        embedder=DeterministicEmbedder(dimension=32),
    ).run(ctx, units=units, nodes=graph.nodes, edges=graph.edges)

    # Build the fake graph adjacency from Phase-2 edges.
    nodes_by_id = {n.node_id: n for n in graph.nodes}
    adjacency: dict[str, list[str]] = {}
    for e in graph.edges:
        adjacency.setdefault(e.src_id, []).append(e.dst_id)
    fake_graph = _FakeGraphRepo(nodes_by_id, adjacency)

    return units, fake_qdrant, fake_graph


async def _run_phase4_packet(units, fake_qdrant, fake_graph, query_text: str):
    embedder = DeterministicEmbedder(dimension=32)
    vector_retriever = VectorRetriever(
        client=fake_qdrant, embedder=embedder, collection="repo_acme",
    )
    graph_retriever = GraphRetriever(fake_graph, max_depth=2)
    # No metadata channel needed here — the test focuses on
    # vector + graph determinism end-to-end.
    hybrid = HybridRetriever(
        planner=QueryPlanner(default_max_depth=2),
        graph=graph_retriever,
        vector=vector_retriever,
        metadata=None,
    )
    seed = next(u for u in units if u.qualified_name == "pkg.utils.add").unit_id
    res = await hybrid.run(
        Query(text=query_text, repo_id="acme", top_k=5, seed_unit_ids=[seed]),
        query_id="qid",
    )
    ranked = RankingModel().rank(res.candidates, top_k=5)
    return ContextAssembler(
        options=AssemblyOptions(max_context_tokens=4000),
    ).build(task=query_text, ranked=ranked)


# ---- Tests ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_phase4_golden_packet_is_deterministic_across_runs() -> None:
    """Two end-to-end retrieval runs over the same fixture must produce
    byte-identical ContextPacket bytes."""
    packets = []
    for _ in range(2):
        units, qd, gr = await _run_phase23()
        pkt = await _run_phase4_packet(units, qd, gr, "auth flow")
        packets.append(pkt.model_dump_json())
    assert packets[0] == packets[1]


@pytest.mark.asyncio
async def test_phase4_golden_packet_has_expected_shape() -> None:
    units, qd, gr = await _run_phase23()
    pkt = await _run_phase4_packet(units, qd, gr, "auth flow")
    assert pkt.task == "auth flow"
    assert isinstance(pkt.context, list)
    assert len(pkt.context) > 0
    assert all(0.0 <= e.score <= 1.0 for e in pkt.context)
    assert 0.0 <= pkt.confidence <= 1.0
    # ContextEntry types respect the priority order constraint:
    # constraints > risks > architecture > logic > code (no code-before-logic).
    types = [e.type for e in pkt.context]
    priority_order = ["constraint", "risk", "architecture", "logic", "code"]
    seen_priority_indices = [priority_order.index(t) for t in types]
    assert seen_priority_indices == sorted(seen_priority_indices)


@pytest.mark.asyncio
async def test_phase4_golden_graph_seed_pulls_in_candidates() -> None:
    """Supplying a seed forces the graph channel to fire and contribute
    candidates. We verify by running the hybrid retriever directly and
    inspecting the per-channel hit count + the seed's presence among
    the fused candidates.
    """
    units, qd, gr = await _run_phase23()
    seed_unit = next(u for u in units if u.qualified_name == "pkg.utils.add")

    embedder = DeterministicEmbedder(dimension=32)
    hybrid = HybridRetriever(
        planner=QueryPlanner(default_max_depth=2),
        graph=GraphRetriever(gr, max_depth=2),
        vector=VectorRetriever(client=qd, embedder=embedder,
                               collection="repo_acme"),
        metadata=None,
    )
    res = await hybrid.run(
        Query(text="auth flow", repo_id="acme", top_k=20,
              seed_unit_ids=[seed_unit.unit_id]),
        query_id="qid",
    )
    # Graph channel produced at least the seed candidate.
    assert res.graph_hits >= 1
    cand_ids = {c.unit_id for c in res.candidates}
    assert seed_unit.unit_id in cand_ids
