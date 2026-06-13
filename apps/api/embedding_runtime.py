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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.config import Settings
from core.config_runtime import RuntimeConfig
from core.embeddings import (
    DEFAULT_LOCAL_MODEL,
    ChunkingStrategy,
    Embedder,
    EmbeddingPipeline,
    LocalEmbedder,
    OpenAIEmbedder,
    local_embedding_dimension,
)

if TYPE_CHECKING:
    from apps.api.state import AppState

# OpenAI text-embedding-3-small (and the legacy deterministic placeholder)
# vector size. Kept here as the canonical constant both routers import.
OPENAI_VECTOR_SIZE = 1536

# Re-embed backfill batch size: a single provider failure loses one batch,
# not the whole repo. Shared by the reembed endpoint and the mode-switch
# reindex so both behave identically.
REINDEX_BATCH_SIZE = 200


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


@dataclass(frozen=True)
class ReindexResult:
    """Outcome of re-embedding one repo's stored units."""

    repo_id: str
    units_total: int
    units_embedded: int
    # Count of failed BATCHES (each up to REINDEX_BATCH_SIZE units).
    failed_batches: int


async def reindex_repo(
    state: AppState,
    settings: Settings,
    runtime: RuntimeConfig,
    repo_id: str,
    *,
    recreate: bool,
) -> ReindexResult:
    """Re-embed a repo's already-ingested units with the current embedder.

    `recreate=True` drops + recreates the collection at the active
    dimension first — required when the embedding mode (and thus the
    dimension) changed, since old-dimension vectors are incompatible.
    `recreate=False` re-embeds in place (the collection is already the
    right size), as the standalone reembed endpoint does.

    When embeddings are disabled (e.g. switched to OpenAI mode with no key)
    the collection is still (re)created at the right dimension but left
    empty — there is no embedder to produce vectors. Batches are isolated:
    a provider failure skips that batch and the run continues.
    """
    collection = f"repo_{repo_id}"
    dimension = active_embedding_dimension(runtime)
    if recreate:
        await state.vector_repo.recreate_collection(collection, dimension)
    else:
        await state.vector_repo.ensure_collection(collection, dimension)

    units = list(await state.units_repo.list_units_for_repo(repo_id))

    embedder = build_runtime_embedder(runtime)
    if embedder is None:
        return ReindexResult(repo_id, len(units), 0, 0)

    pipeline = EmbeddingPipeline(
        embedder=embedder,
        chunker=ChunkingStrategy(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        ),
        vector_repo=state.vector_repo,
    )
    embedded = 0
    failed_batches = 0
    try:
        for i in range(0, len(units), REINDEX_BATCH_SIZE):
            batch = units[i : i + REINDEX_BATCH_SIZE]
            try:
                await pipeline.run(batch, collection=collection)
            except Exception:
                failed_batches += 1
            else:
                embedded += len(batch)
    finally:
        # aclose isn't on the Embedder Protocol; both concrete embedders
        # have it (no-op for local, releases the HTTP client for openai).
        aclose = getattr(embedder, "aclose", None)
        if aclose is not None:
            await aclose()
    return ReindexResult(repo_id, len(units), embedded, failed_batches)


__all__ = [
    "OPENAI_VECTOR_SIZE",
    "REINDEX_BATCH_SIZE",
    "ReindexResult",
    "active_embedding_dimension",
    "build_runtime_embedder",
    "reindex_repo",
]
