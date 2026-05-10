from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.dependencies import AppStateDep
from core.ingestion import IngestionPipeline, make_context

router = APIRouter(prefix="/ingest", tags=["ingestion"])

# Phase 2 hard cap: until embeddings land in Phase 3, the Qdrant
# collection vector size is fixed by config. We pin to 1536 (OpenAI
# small / Voyage default) when no explicit dimension is provided.
_DEFAULT_VECTOR_SIZE = 1536


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
    result = await IngestionPipeline().run(ctx)
    return IngestResponse(
        repo_id=result.repo_id,
        commit_sha=result.commit_sha,
        units_collection=collection,
        metrics=dict(result.metrics),
        failed_files=list(result.failed_files),
    )
