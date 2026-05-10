"""Phase-3 golden gate: parse + build + compress over the fixture repo.

Runs the full Phase-2 + Phase-3 path twice and asserts byte-equal
dense outputs across both runs. If this test fails, something
non-deterministic crept into the encoders, summarizers, chunker, or
embedder — fix the root cause, do not relax the assertion.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.compression import CompressionContext
from core.compression.pipeline import CompressionPipeline
from core.embeddings import ChunkingStrategy, DeterministicEmbedder
from core.ingestion import GraphBuilder
from core.parsing import FileWalker, PythonParser
from schemas import CompressionMetrics

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def _ingest_fixture():
    walk = FileWalker().walk(FIXTURE_REPO, repo_id="acme")
    parser = PythonParser()
    units = []
    for ref in walk.files:
        text = (FIXTURE_REPO / ref.path).read_text(encoding="utf-8")
        units.extend(parser.parse_file(
            source=text,
            repo_id="acme",
            file_path=ref.path,
            commit_sha="commit-deadbeef",
        ))
    graph = GraphBuilder().build(units)
    return units, graph


def _ctx() -> tuple[CompressionContext, AsyncMock]:
    repo = AsyncMock()
    repo.ensure_collection = AsyncMock()
    repo.upsert_payloads = AsyncMock(side_effect=lambda c, pts: len(list(pts)))
    return CompressionContext(
        repo_id="acme",
        commit_sha="commit-deadbeef",
        units_collection="repo_acme",
        vector_repo=repo,
        metrics=CompressionMetrics(),
    ), repo


def _pipeline() -> CompressionPipeline:
    return CompressionPipeline(
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        embedder=DeterministicEmbedder(dimension=32),
    )


@pytest.mark.asyncio
async def test_phase3_golden_is_byte_deterministic_across_runs() -> None:
    streams: list[dict] = []
    for _ in range(2):
        units, graph = _ingest_fixture()
        ctx, repo = _ctx()
        result = await _pipeline().run(
            ctx, units=units, nodes=graph.nodes, edges=graph.edges,
        )
        sent_points = list(repo.upsert_payloads.call_args.args[1])
        streams.append({
            "modules": [m.to_dense_json() for m in result.dense_modules],
            "apis": [a.to_dense_json() for a in result.dense_apis],
            "slices": [s.to_dense_json() for s in result.dense_graph_slices],
            "vectors": [
                {"point_id": p.point_id, "vector": list(p.vector or [])}
                for p in sorted(sent_points, key=lambda p: p.point_id)
            ],
        })

    assert json.dumps(streams[0], sort_keys=True) == json.dumps(streams[1], sort_keys=True)


@pytest.mark.asyncio
async def test_phase3_golden_extracts_module_and_api_records() -> None:
    units, graph = _ingest_fixture()
    ctx, _repo = _ctx()
    result = await _pipeline().run(
        ctx, units=units, nodes=graph.nodes, edges=graph.edges,
    )

    module_ids = {m.id for m in result.dense_modules}
    # Fixture has these module qnames (see tests/fixtures/sample_repo).
    assert {"pkg", "pkg.utils", "pkg.services", "pkg.services.auth"}.issubset(module_ids)

    auth_module = next(m for m in result.dense_modules if m.id == "pkg.services.auth")
    assert "TokenStore" in auth_module.cls
    assert "InMemoryTokenStore" in auth_module.cls
    assert "login" in auth_module.fn
    assert "refresh" in auth_module.fn
    # Imports captured by parser → module dense imp list.
    assert "abc.ABC" in auth_module.imp
    assert any(i.endswith("retry") or i.endswith("add") for i in auth_module.imp)

    # API surface for utils.py = public function names.
    utils_api = next(a for a in result.dense_apis if a.id == "pkg.utils")
    assert utils_api.api == ["add", "retry"]


@pytest.mark.asyncio
async def test_phase3_golden_writes_one_embedding_per_unit() -> None:
    units, graph = _ingest_fixture()
    ctx, repo = _ctx()
    result = await _pipeline().run(
        ctx, units=units, nodes=graph.nodes, edges=graph.edges,
    )
    sent_points = list(repo.upsert_payloads.call_args.args[1])
    assert len(sent_points) == len(units)
    # Cross-store identity invariant intact: point_id == unit_id.
    assert {p.point_id for p in sent_points} == {u.unit_id for u in units}
    # Token reduction is positive on the fixture.
    assert result.metrics["token_reduction_ratio"] > 0


@pytest.mark.asyncio
async def test_phase3_golden_token_reduction_is_meaningful() -> None:
    units, graph = _ingest_fixture()
    ctx, _repo = _ctx()
    await _pipeline().run(ctx, units=units, nodes=graph.nodes, edges=graph.edges)
    # The fixture has ~25 units totaling ~1.5KB of source; dense
    # projection should compress by >40%.
    assert ctx.metrics.bytes_input > ctx.metrics.bytes_output
    assert ctx.metrics.token_reduction_ratio() > 0.4
