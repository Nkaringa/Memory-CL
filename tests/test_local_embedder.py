from __future__ import annotations

import math
from collections.abc import Iterator

import pytest

from core.embeddings import (
    DEFAULT_LOCAL_MODEL,
    Embedder,
    LocalEmbedder,
    local_embedding_dimension,
)
from core.embeddings.local_embedder import MAX_INPUT_CHARS


class _FakeVec:
    """Stand-in for a fastembed float32 numpy array (only `tolist` used)."""

    def __init__(self, data: list[float]) -> None:
        self._data = data

    def tolist(self) -> list[float]:
        return self._data


class _FakeModel:
    """Fake `TextEmbedding`: records calls, yields one vector per input.

    Mirrors fastembed's contract — `embed` returns a generator of arrays
    in input order, bounded by `batch_size`.
    """

    def __init__(self, dimension: int) -> None:
        self._dimension = dimension
        self.seen_texts: list[str] = []
        self.seen_batch_size: int | None = None

    def embed(self, texts: list[str], batch_size: int = 0) -> Iterator[_FakeVec]:
        self.seen_texts = list(texts)
        self.seen_batch_size = batch_size
        for i, _t in enumerate(texts):
            # A simple, input-dependent vector so order is verifiable.
            yield _FakeVec([float(i + 1)] * self._dimension)


def _embedder_with_fake(dimension: int = 384, **kwargs: object) -> tuple[LocalEmbedder, _FakeModel]:
    """LocalEmbedder pre-loaded with a fake model — no real fastembed load."""
    emb = LocalEmbedder(**kwargs)  # type: ignore[arg-type]
    fake = _FakeModel(dimension)
    emb._model = fake  # bypass lazy load
    return emb, fake


def test_satisfies_embedder_protocol() -> None:
    emb, _ = _embedder_with_fake()
    assert isinstance(emb, Embedder)


def test_dimension_known_without_loading_model() -> None:
    # Constructing must NOT load the model (no network); dimension comes
    # from the static table.
    emb = LocalEmbedder()
    assert emb.dimension == 384
    assert emb._model is None


def test_local_embedding_dimension_default() -> None:
    assert local_embedding_dimension() == 384
    assert local_embedding_dimension(DEFAULT_LOCAL_MODEL) == 384
    assert local_embedding_dimension("BAAI/bge-base-en-v1.5") == 768


def test_name_is_local() -> None:
    emb = LocalEmbedder()
    assert emb.name == "local"


async def test_embed_batch_empty_returns_empty_without_loading() -> None:
    emb = LocalEmbedder()
    assert await emb.embed_batch([]) == []
    assert emb._model is None  # never loaded for an empty batch


async def test_embed_batch_returns_one_vector_per_input_in_order() -> None:
    emb, fake = _embedder_with_fake(dimension=384)
    out = await emb.embed_batch(["alpha", "beta", "gamma"])
    assert len(out) == 3
    assert fake.seen_texts == ["alpha", "beta", "gamma"]
    assert all(len(v) == 384 for v in out)
    assert all(isinstance(v, tuple) for v in out)
    # Order-dependent fake vectors: input i -> all-(i+1).
    assert out[0][0] == 1.0
    assert out[1][0] == 2.0
    assert out[2][0] == 3.0


async def test_embed_batch_passes_configured_batch_size() -> None:
    emb, fake = _embedder_with_fake(dimension=384, batch_size=64)
    await emb.embed_batch(["x"])
    assert fake.seen_batch_size == 64


async def test_embed_batch_truncates_oversized_input() -> None:
    emb, fake = _embedder_with_fake(dimension=384)
    huge = "z" * (MAX_INPUT_CHARS + 5_000)
    await emb.embed_batch([huge])
    assert len(fake.seen_texts[0]) == MAX_INPUT_CHARS


async def test_embed_batch_rejects_dimension_mismatch() -> None:
    # Fake model emits 99-dim vectors but the embedder expects 384 — the
    # guard must raise rather than let a mis-sized vector reach storage.
    emb, _ = _embedder_with_fake(dimension=99)
    with pytest.raises(ValueError, match="expected 384"):
        await emb.embed_batch(["oops"])


def test_rejects_nonpositive_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size must be > 0"):
        LocalEmbedder(batch_size=0)


def test_unknown_model_dimension_raises() -> None:
    with pytest.raises(ValueError, match="unknown local embedding model"):
        local_embedding_dimension("definitely/not-a-real-model-xyz")


@pytest.mark.integration
async def test_real_model_embeds_384_normalized() -> None:
    """Loads the REAL bge model (downloads on cold cache). Verifies the
    contract the unit tests fake: 384-dim, L2-normalized, deterministic."""
    emb = LocalEmbedder()
    out = await emb.embed_batch(["def add(a, b): return a + b", "adds two numbers"])
    assert len(out) == 2
    assert all(len(v) == 384 for v in out)
    for v in out:
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 1e-3
    # Code and its description should be more similar than orthogonal.
    cos = sum(a * b for a, b in zip(out[0], out[1], strict=True))
    assert cos > 0.2
