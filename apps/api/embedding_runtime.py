"""Single source of truth for the embedder selected by `RuntimeConfig`.

The query side (`lifespan`) and the document side (`ingest`) MUST embed
with the same model at the same dimension — otherwise cosine scores
against the stored vectors are noise (the "dimension gotcha"). Both build
their embedder through `build_runtime_embedder` here, and both size the
Qdrant collection through `active_embedding_dimension`, so the two can
never silently diverge.

Mode resolution (Postgres-over-env via `RuntimeConfig`):
  * disabled            -> None / 1536 (deterministic placeholder size)
  * 'local'             -> LocalEmbedder (bge-small, 384-dim)
  * 'openai'            -> OpenAIEmbedder (text-embedding-3-small, 1536-dim)

`RuntimeConfig` lives in `core` but is storage-coupled, so this selector
sits in the `apps/api` layer rather than inside `core/embeddings` (which
stays storage-agnostic).
"""

from __future__ import annotations

from core.config_runtime import RuntimeConfig
from core.embeddings import (
    DEFAULT_LOCAL_MODEL,
    Embedder,
    LocalEmbedder,
    OpenAIEmbedder,
    local_embedding_dimension,
)

# OpenAI text-embedding-3-small (and the legacy deterministic placeholder)
# vector size. Kept here as the canonical constant both routers import.
OPENAI_VECTOR_SIZE = 1536


def build_runtime_embedder(runtime: RuntimeConfig) -> Embedder | None:
    """Construct the embedder the runtime config selects, or None when off.

    Returns None when embeddings are disabled so callers fall back to the
    deterministic placeholder. The returned embedder always exposes an
    async `aclose()` (no-op for the local embedder) so callers can release
    it uniformly.
    """
    if not runtime.embeddings_enabled():
        return None
    if runtime.embedding_mode() == "local":
        return LocalEmbedder(model=DEFAULT_LOCAL_MODEL)
    api_key = runtime.openai_api_key()
    assert api_key is not None  # embeddings_enabled() guarantees it in openai mode
    return OpenAIEmbedder(
        api_key=api_key,
        model=runtime.embedding_model(),
        dimension=OPENAI_VECTOR_SIZE,
    )


def active_embedding_dimension(runtime: RuntimeConfig) -> int:
    """Vector size for the active mode — used to size Qdrant collections.

    Resolvable WITHOUT constructing an embedder or loading a model: local
    dimension comes from the model's static metadata, OpenAI/placeholder is
    the fixed 1536. The collection MUST be created at this size or the
    upsert of real vectors is rejected mid-batch.
    """
    if runtime.embeddings_enabled() and runtime.embedding_mode() == "local":
        return local_embedding_dimension(DEFAULT_LOCAL_MODEL)
    return OPENAI_VECTOR_SIZE


__all__ = [
    "OPENAI_VECTOR_SIZE",
    "active_embedding_dimension",
    "build_runtime_embedder",
]
