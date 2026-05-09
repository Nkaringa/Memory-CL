from __future__ import annotations

import math
from unittest.mock import AsyncMock

import pytest

from core.embeddings import (
    ChunkingStrategy,
    DeterministicEmbedder,
    Embedder,
    EmbeddingPipeline,
)
from core.embeddings.chunking_strategy import estimate_tokens
from schemas import IngestionUnit, Language, UnitKind, content_sha, stable_unit_id


def _u(qname: str, content: str, kind: UnitKind = UnitKind.FUNCTION) -> IngestionUnit:
    return IngestionUnit(
        unit_id=stable_unit_id("r", "pkg/m.py", qname),
        repo_id="r",
        commit_sha="c",
        kind=kind,
        name=qname.rsplit(".", 1)[-1],
        qualified_name=qname,
        parent_qualified_name="pkg.m",
        file_path="pkg/m.py",
        language=Language.PYTHON,
        line_start=1,
        line_end=max(1, content.count("\n") + 1),
        content=content,
        source_sha=content_sha(content),
    )


# ---- ChunkingStrategy ------------------------------------------------------
def test_chunking_rejects_invalid_knobs() -> None:
    with pytest.raises(ValueError):
        ChunkingStrategy(chunk_size=0, chunk_overlap=0)
    with pytest.raises(ValueError):
        ChunkingStrategy(chunk_size=10, chunk_overlap=10)
    with pytest.raises(ValueError):
        ChunkingStrategy(chunk_size=10, chunk_overlap=-1)


def test_chunking_short_content_yields_single_chunk() -> None:
    cs = ChunkingStrategy(chunk_size=400, chunk_overlap=40)
    chunks = cs.chunk_unit(_u("pkg.m.f", "def f(): return 1\n"))
    assert len(chunks) == 1
    assert chunks[0].seq == 0
    assert chunks[0].chunk_id.endswith("#c0")
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len("def f(): return 1\n")


def test_chunking_long_content_yields_overlapping_chunks() -> None:
    # chunk_size=10 tokens ~ 40 chars; overlap=2 tokens ~ 8 chars; stride=32.
    cs = ChunkingStrategy(chunk_size=10, chunk_overlap=2)
    src = "x" * 100
    chunks = cs.chunk_unit(_u("pkg.m.long", src))
    assert len(chunks) >= 3
    # Each chunk respects the chunk-size cap.
    assert all((c.char_end - c.char_start) <= 40 for c in chunks)
    # Overlap: every chunk after the first starts before the previous ended.
    from itertools import pairwise
    for prev, nxt in pairwise(chunks):
        assert nxt.char_start < prev.char_end
    # Coverage: last chunk reaches the end.
    assert chunks[-1].char_end == len(src)


def test_chunking_empty_content_returns_empty_list() -> None:
    cs = ChunkingStrategy(chunk_size=100, chunk_overlap=10)
    assert cs.chunk_unit(_u("pkg.m.empty", "")) == []


def test_chunking_is_deterministic() -> None:
    cs = ChunkingStrategy(chunk_size=10, chunk_overlap=2)
    src = "abcdefgh" * 20
    a = cs.chunk_unit(_u("pkg.m.x", src))
    b = cs.chunk_unit(_u("pkg.m.x", src))
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert [(c.char_start, c.char_end) for c in a] == \
           [(c.char_start, c.char_end) for c in b]


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


# ---- DeterministicEmbedder -------------------------------------------------
def test_embedder_satisfies_protocol() -> None:
    e = DeterministicEmbedder(dimension=64)
    assert isinstance(e, Embedder)
    assert e.dimension == 64


@pytest.mark.asyncio
async def test_embedder_is_deterministic_and_normalized() -> None:
    e = DeterministicEmbedder(dimension=128)
    a = await e.embed_batch(["hello world", "another text"])
    b = await e.embed_batch(["hello world", "another text"])
    assert a == b
    # Vectors are L2-normalized to unit length.
    for v in a:
        assert len(v) == 128
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 1e-9


@pytest.mark.asyncio
async def test_embedder_distinct_texts_produce_distinct_vectors() -> None:
    e = DeterministicEmbedder(dimension=64)
    [v1, v2] = await e.embed_batch(["hello", "world"])
    assert v1 != v2


def test_embedder_rejects_zero_dimension() -> None:
    with pytest.raises(ValueError):
        DeterministicEmbedder(dimension=0)


# ---- EmbeddingPipeline -----------------------------------------------------
def _make_vector_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.ensure_collection = AsyncMock()
    repo.upsert_payloads = AsyncMock(side_effect=lambda c, pts: len(list(pts)))
    return repo


@pytest.mark.asyncio
async def test_pipeline_writes_one_vector_per_unit() -> None:
    units = [
        _u("pkg.m.a", "def a(): return 1\n"),
        _u("pkg.m.b", "def b(): return 2\n"),
        _u("pkg.m.c", "def c(): return 3\n"),
    ]
    repo = _make_vector_repo()
    pipe = EmbeddingPipeline(
        embedder=DeterministicEmbedder(dimension=32),
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        vector_repo=repo,
    )
    res = await pipe.run(units, collection="repo:r")
    assert res.vectors_written == 3
    repo.ensure_collection.assert_awaited_once_with("repo:r", 32)

    sent_points = repo.upsert_payloads.call_args.args[1]
    sent_points = list(sent_points)
    assert len(sent_points) == 3
    # Cross-store identity invariant: point_id == unit_id.
    assert {p.point_id for p in sent_points} == {u.unit_id for u in units}
    # All vectors present and dimension-correct.
    assert all(p.vector is not None and len(p.vector) == 32 for p in sent_points)


@pytest.mark.asyncio
async def test_pipeline_handles_unit_with_empty_content() -> None:
    units = [_u("pkg.m.empty", "", kind=UnitKind.MODULE)]
    repo = _make_vector_repo()
    pipe = EmbeddingPipeline(
        embedder=DeterministicEmbedder(dimension=16),
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        vector_repo=repo,
    )
    res = await pipe.run(units, collection="c")
    assert res.vectors_written == 1
    sent = list(repo.upsert_payloads.call_args.args[1])
    assert sent[0].vector is None  # placeholder pathway, no embedding


@pytest.mark.asyncio
async def test_pipeline_is_byte_deterministic_across_runs() -> None:
    units = [_u("pkg.m.f", "def f(): return 1\n")]
    repo_a = _make_vector_repo()
    repo_b = _make_vector_repo()
    pipe_a = EmbeddingPipeline(
        embedder=DeterministicEmbedder(dimension=32),
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        vector_repo=repo_a,
    )
    pipe_b = EmbeddingPipeline(
        embedder=DeterministicEmbedder(dimension=32),
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        vector_repo=repo_b,
    )
    await pipe_a.run(units, collection="c")
    await pipe_b.run(units, collection="c")
    sent_a = list(repo_a.upsert_payloads.call_args.args[1])
    sent_b = list(repo_b.upsert_payloads.call_args.args[1])
    assert [p.vector for p in sent_a] == [p.vector for p in sent_b]
