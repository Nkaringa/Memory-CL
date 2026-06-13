from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

from core.compression.logevent import emit_phase3_event
from core.logging import get_logger
from core.observability import get_tracer

_tracer = get_tracer("core.embeddings.local_embedder")
_log = get_logger(__name__)

# The default local model. BAAI/bge-small-en-v1.5 is a strong, tiny
# (~130 MB ONNX) general-purpose retrieval model that fastembed ships as
# a first-class quantized model. It emits 384-dim, already-L2-normalized
# vectors — so cosine similarity behaves like inner product, matching the
# OpenAI path's expectations.
DEFAULT_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"

# Known output dimensions for the local models we support, so the active
# embedding dimension is resolvable WITHOUT loading the (slow, network-
# fetching) model — `ensure_collection` needs the size up front, before
# any embedder is constructed. Any model not listed here falls back to
# loading the model once and reading its dimension.
_MODEL_DIMENSIONS: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
}

# Defensive per-input cap mirrors the OpenAI path: a pathologically large
# unit gets its tail truncated rather than blowing up the batch. bge's
# context window is 512 tokens (~2000 chars); chunking upstream keeps real
# units well under this, so truncation is a backstop, not the norm.
MAX_INPUT_CHARS = 8_000


def local_embedding_dimension(model: str = DEFAULT_LOCAL_MODEL) -> int:
    """Output dimension for a local model, resolvable without loading it.

    Used by the ingest path to size the Qdrant collection BEFORE the
    embedder exists. Known models answer from the static table; an unknown
    model triggers a one-time model load to introspect its dimension
    (`TextEmbedding` exposes per-model metadata) — slow, but correct.
    """
    dim = _MODEL_DIMENSIONS.get(model)
    if dim is not None:
        return dim
    from fastembed import TextEmbedding

    for desc in TextEmbedding.list_supported_models():
        if desc.get("model") == model:
            return int(desc["dim"])
    raise ValueError(f"unknown local embedding model: {model!r}")


class LocalEmbedder:
    """On-device embedder over fastembed (ONNX) — no API key, no network
    at query time once the model is cached.

    Satisfies the `Embedder` Protocol. fastembed's `TextEmbedding` is
    synchronous and CPU-bound, so `embed_batch` offloads the encode to a
    worker thread (`asyncio.to_thread`) to keep the event loop responsive.

    The model is loaded LAZILY on first embed (not in `__init__`): loading
    downloads ~130 MB on a cold cache and initializes an ONNX session,
    which must not block process startup when this is wired as the query
    embedder in `lifespan`. The first request after boot pays that cost
    once; every request after reuses the warm session. A lock serializes
    the one-time load so concurrent first-requests don't double-init.

    fastembed emits float32 numpy arrays; we return `tuple[float, ...]`
    to honour the Protocol (hashable, dedup-friendly). bge models are
    already L2-normalized by fastembed, so no extra normalization here.
    """

    name: str = "local"

    def __init__(
        self,
        *,
        model: str = DEFAULT_LOCAL_MODEL,
        batch_size: int = 128,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        self._model_name = model
        self._batch_size = batch_size
        self._dimension = local_embedding_dimension(model)
        self._model: object | None = None
        self._load_lock = asyncio.Lock()

    @property
    def dimension(self) -> int:
        return self._dimension

    async def aclose(self) -> None:
        """No-op teardown — symmetry with `OpenAIEmbedder.aclose` so callers
        can release any embedder uniformly. fastembed holds no sockets; the
        ONNX session is freed with the object."""
        return None

    async def _ensure_model(self) -> object:
        """Load the ONNX model once, serialized against concurrent first-use."""
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:  # another task won the race
                return self._model
            start = time.perf_counter()
            # Import inside the method so merely importing this module (and
            # the embeddings package) doesn't drag in fastembed/onnxruntime
            # — keeps OpenAI-mode and test imports light.
            from fastembed import TextEmbedding

            model = await asyncio.to_thread(
                TextEmbedding, model_name=self._model_name
            )
            self._model = model
            _log.info(
                "local_embedder_model_loaded",
                model=self._model_name,
                load_ms=round((time.perf_counter() - start) * 1000, 1),
            )
            return model

    async def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        start = time.perf_counter()
        truncated = [t[:MAX_INPUT_CHARS] for t in texts]
        model = await self._ensure_model()
        with _tracer.start_as_current_span("local_embedder.embed_batch") as span:
            span.set_attribute("count", len(truncated))
            span.set_attribute("model", self._model_name)
            vectors = await asyncio.to_thread(self._encode, model, truncated)
            # Validate dimension BEFORE anything is stored: a mis-sized
            # vector would poison the collection or be rejected by Qdrant
            # mid-batch (mirrors the OpenAI embedder's guard).
            for v in vectors:
                if len(v) != self._dimension:
                    raise ValueError(
                        f"local embedder produced dimension {len(v)}, "
                        f"expected {self._dimension}"
                    )
            emit_phase3_event(
                event="local_embed_batch",
                operation="embed",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                level="info",
                texts=len(truncated),
                vectors=len(vectors),
                model=self._model_name,
                embedder=self.name,
            )
            return vectors

    def _encode(self, model: object, texts: list[str]) -> list[tuple[float, ...]]:
        """Run the synchronous fastembed encode (called in a worker thread).

        `TextEmbedding.embed` returns a generator of float32 numpy arrays
        in input order; `batch_size` bounds the internal ONNX batch.
        """
        out: list[tuple[float, ...]] = []
        for arr in model.embed(texts, batch_size=self._batch_size):  # type: ignore[attr-defined]
            out.append(tuple(float(x) for x in arr.tolist()))
        return out


__all__ = ["DEFAULT_LOCAL_MODEL", "LocalEmbedder", "local_embedding_dimension"]
