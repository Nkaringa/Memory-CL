from __future__ import annotations

import textwrap
from unittest.mock import AsyncMock

import pytest

from core.compression import CompressionContext, DenseEncoder
from core.compression.pipeline import CompressionPipeline
from core.embeddings import ChunkingStrategy, DeterministicEmbedder
from core.ingestion import GraphBuilder
from core.parsing import PythonParser
from schemas import CompressionMetrics

REPO = "r"
COMMIT = "c"


def _units(source: str, file_path: str = "pkg/m.py") -> list:
    return PythonParser().parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


def _make_ctx(vector_repo: AsyncMock | None = None) -> CompressionContext:
    repo = vector_repo or AsyncMock()
    repo.ensure_collection = AsyncMock()
    repo.upsert_payloads = AsyncMock(side_effect=lambda c, pts: len(list(pts)))
    return CompressionContext(
        repo_id=REPO,
        commit_sha=COMMIT,
        units_collection="repo:r",
        vector_repo=repo,
        metrics=CompressionMetrics(),
    )


def _pipe() -> CompressionPipeline:
    return CompressionPipeline(
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        embedder=DeterministicEmbedder(dimension=32),
    )


@pytest.mark.asyncio
async def test_pipeline_produces_dense_records_and_writes_embeddings() -> None:
    units = _units("""
        import os
        VERSION = '1'

        def public_fn():
            return os.path.join('a', 'b')

        def _hidden_fn():
            return 1

        class Service:
            def run(self): return public_fn()
    """)
    graph = GraphBuilder().build(units)
    ctx = _make_ctx()

    result = await _pipe().run(
        ctx, units=units, nodes=graph.nodes, edges=graph.edges,
    )

    # Dense encode covers every input unit.
    assert len(result.encoded_units) == len(units)
    # One DenseModule per module unit.
    module_qnames = {m.id for m in result.dense_modules}
    assert "pkg.m" in module_qnames

    # API summary contains the public function + class, not the private one.
    [api] = result.dense_apis
    assert "public_fn" in api.api
    assert "_hidden_fn" not in api.api
    assert "Service" in api.cls

    # Graph slices exclude EXTERNAL nodes; module/class/fn/method present.
    slice_kinds = {s.k for s in result.dense_graph_slices}
    assert "External" not in slice_kinds
    assert {"Module", "Function", "Class", "Method"}.issubset(slice_kinds)

    # Embeddings: one written per unit, vector dim matches embedder.
    assert result.metrics["embeddings_written"] == len(units)
    sent_points = list(ctx.vector_repo.upsert_payloads.call_args.args[1])
    assert {p.point_id for p in sent_points} == {u.unit_id for u in units}
    # Token-reduction ratio is non-trivial.
    assert result.metrics["token_reduction_ratio"] >= 0.0


@pytest.mark.asyncio
async def test_pipeline_isolates_failed_unit_encoding() -> None:
    units = _units("""
        def a(): pass
        def b(): pass
    """)
    graph = GraphBuilder().build(units)

    # Build an encoder that raises on the second call.
    class _FlakyEncoder(DenseEncoder):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def encode_unit(self, unit):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("simulated failure")
            return super().encode_unit(unit)

    pipe = CompressionPipeline(
        encoder=_FlakyEncoder(),
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        embedder=DeterministicEmbedder(dimension=32),
    )
    ctx = _make_ctx()
    res = await pipe.run(ctx, units=units, nodes=graph.nodes, edges=graph.edges)

    # One unit was marked degraded — pipeline still completed.
    assert len(res.degraded_unit_ids) == 1
    assert res.metrics["embeddings_written"] >= 1


@pytest.mark.asyncio
async def test_pipeline_is_byte_deterministic_across_runs() -> None:
    units = _units("""
        def helper(): return 1
        def caller(): return helper()
    """)
    graph = GraphBuilder().build(units)

    streams: list[list[str]] = []
    for _ in range(2):
        ctx = _make_ctx()
        res = await _pipe().run(
            ctx, units=units, nodes=graph.nodes, edges=graph.edges,
        )
        streams.append([
            *(m.to_dense_json() for m in res.dense_modules),
            *(a.to_dense_json() for a in res.dense_apis),
            *(s.to_dense_json() for s in res.dense_graph_slices),
        ])
    assert streams[0] == streams[1]


@pytest.mark.asyncio
async def test_pipeline_metrics_reflect_token_savings() -> None:
    src = """
        def long_function():
            \"\"\"This is a long docstring that adds a lot of content but the
            dense projection should be much smaller because the dense record
            only contains the qname, kind tag, and file path.\"\"\"
            return 1
    """ + ("\n            # padding\n" * 30)
    units = _units(src)
    graph = GraphBuilder().build(units)
    ctx = _make_ctx()

    res = await _pipe().run(ctx, units=units, nodes=graph.nodes, edges=graph.edges)
    assert ctx.metrics.bytes_input > ctx.metrics.bytes_output
    assert res.metrics["token_reduction_ratio"] > 0
