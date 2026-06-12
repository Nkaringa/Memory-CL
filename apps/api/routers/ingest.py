from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.dependencies import AppStateDep
from apps.api.state import AppState
from core import get_settings
from core.config import Settings
from core.embeddings import ChunkingStrategy, EmbeddingPipeline, OpenAIEmbedder
from core.ingestion import IngestionPipeline, make_context

router = APIRouter(prefix="/ingest", tags=["ingestion"])

# Phase 2 hard cap: until embeddings land in Phase 3, the Qdrant
# collection vector size is fixed by config. We pin to 1536 (OpenAI
# small / Voyage default) when no explicit dimension is provided.
_DEFAULT_VECTOR_SIZE = 1536

# Reembed backfill processes units in fixed-size batches so a single
# provider failure only loses one batch, not the whole repo.
_REEMBED_BATCH_SIZE = 200


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str = Field(min_length=1, max_length=128)
    repo_path: str = Field(description="Absolute path to the repo on the API host")
    commit_sha: str = Field(min_length=1, max_length=64)


class IngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    commit_sha: str
    units_collection: str
    metrics: dict[str, float | int]
    failed_files: list[str]


class ReembedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str = Field(min_length=1, max_length=128)


class ReembedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str
    units_total: int
    units_embedded: int
    failed: int


def _build_embedding_components(
    state: AppState, settings: Settings
) -> tuple[EmbeddingPipeline, OpenAIEmbedder] | None:
    """Construct the Phase-3 embedding stack, or None when disabled.

    Returned as a (pipeline, embedder) pair so callers can `aclose()`
    the embedder's HTTP client after the request. Module-level function
    (not a closure) so tests can monkeypatch it with a fake pipeline.
    """
    if not settings.embeddings_enabled:
        return None
    assert settings.openai_api_key is not None  # embeddings_enabled guarantees it
    embedder = OpenAIEmbedder(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.embedding_model,
        dimension=_DEFAULT_VECTOR_SIZE,
    )
    pipeline = EmbeddingPipeline(
        embedder=embedder,
        chunker=ChunkingStrategy(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
        ),
        vector_repo=state.vector_repo,
    )
    return pipeline, embedder


@router.post(
    "",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
)
async def ingest_repo(req: IngestRequest, state: AppStateDep) -> IngestResponse:
    """Trigger ingestion of `repo_path` under tenant `repo_id` at `commit_sha`.

    Phase 2 contract: idempotent — re-running the same (repo_id, commit_sha)
    on identical content is a no-op for unchanged units. Per-file failures
    are isolated and reported via `failed_files`.

    Phase 3: when an OpenAI key is configured, changed units get real
    vectors as part of the same run; embedding failures degrade to the
    placeholder behavior and never fail the ingest.
    """
    repo_root = Path(req.repo_path)
    # ASYNC240: a single-shot stat() during request setup is fine —
    # the alternative (anyio.Path) adds a dependency for one call.
    if not repo_root.exists() or not repo_root.is_dir():  # noqa: ASYNC240
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"repo_path is not a directory: {repo_root}",
        )

    # Qdrant ≥1.11 rejects ":" in collection names with a 422. Use "_"
    # as the separator to keep names unambiguous + accepted everywhere.
    # The user-facing repo_id is unchanged; only the storage-layer name
    # is rewritten.
    collection = f"repo_{req.repo_id}"
    await state.vector_repo.ensure_collection(collection, _DEFAULT_VECTOR_SIZE)

    ctx = make_context(
        repo_id=req.repo_id,
        repo_path=repo_root,
        commit_sha=req.commit_sha,
        units_collection=collection,
        units_repo=state.units_repo,
        graph_repo=state.graph_repo,
        vector_repo=state.vector_repo,
    )
    components = _build_embedding_components(state, get_settings())
    try:
        result = await IngestionPipeline(
            embedding_pipeline=components[0] if components else None,
        ).run(ctx)
    finally:
        if components is not None:
            await components[1].aclose()
    return IngestResponse(
        repo_id=result.repo_id,
        commit_sha=result.commit_sha,
        units_collection=collection,
        metrics=dict(result.metrics),
        failed_files=list(result.failed_files),
    )


@router.post(
    "/reembed",
    response_model=ReembedResponse,
    status_code=status.HTTP_200_OK,
)
async def reembed_repo(req: ReembedRequest, state: AppStateDep) -> ReembedResponse:
    """Backfill real vectors for every unit already ingested for `repo_id`.

    Use after configuring OPENAI_API_KEY on a deployment that ingested
    with placeholder vectors, or after an embed-degraded ingest. Batches
    are independent: a provider failure skips that batch (counted in
    `failed`) and the run continues.
    """
    settings = get_settings()
    if not settings.embeddings_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="embeddings are disabled — set OPENAI_API_KEY to enable reembed",
        )
    components = _build_embedding_components(state, settings)
    assert components is not None  # embeddings_enabled checked above
    pipeline, embedder = components

    units = list(await state.units_repo.list_units_for_repo(req.repo_id))
    collection = f"repo_{req.repo_id}"

    embedded = 0
    failed = 0
    try:
        for i in range(0, len(units), _REEMBED_BATCH_SIZE):
            batch = units[i : i + _REEMBED_BATCH_SIZE]
            try:
                await pipeline.run(batch, collection=collection)
            except Exception:
                # Batch-level isolation: keep going, report the count.
                failed += 1
            else:
                embedded += len(batch)
    finally:
        await embedder.aclose()

    return ReembedResponse(
        repo_id=req.repo_id,
        units_total=len(units),
        units_embedded=embedded,
        failed=failed,
    )
