from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.auth_deps import SoftPrincipalDep
from apps.api.dependencies import AppStateDep
from apps.api.embedding_runtime import (
    OPENAI_VECTOR_SIZE,
    active_embedding_dimension,
    build_runtime_embedder,
)
from apps.api.state import AppState
from apps.api.repo_access import assert_repo_access
from apps.mcp.auth import ApiKeyDep
from core import get_settings
from core.config import Settings
from core.config_runtime import RuntimeConfig
from core.embeddings import ChunkingStrategy, Embedder, EmbeddingPipeline, OpenAIEmbedder
from core.ingestion import IngestionPipeline, make_context

router = APIRouter(prefix="/ingest", tags=["ingestion"])

# Default Qdrant vector size when no runtime config is available (legacy /
# test path): the OpenAI small / deterministic-placeholder dimension. The
# production path sizes the collection from `active_embedding_dimension`
# (384 in local mode, 1536 in openai mode) so the collection always
# matches the embedder that will write to it.
_DEFAULT_VECTOR_SIZE = OPENAI_VECTOR_SIZE

# Reembed backfill processes units in fixed-size batches so a single
# provider failure only loses one batch, not the whole repo.
_REEMBED_BATCH_SIZE = 200

# Per-repo in-flight guard: two concurrent reembeds of the same repo
# would double-spend on the provider and race writes to the same Qdrant
# points. Plain set is safe here — uvicorn runs a single process and the
# check-then-add below has no awaits in between, so it's atomic within
# the event loop. Multi-worker deployments need a shared lock (Redis) —
# Phase-4 work.
_REEMBED_IN_FLIGHT: set[str] = set()


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
    # Count of failed BATCHES (each up to _REEMBED_BATCH_SIZE units),
    # not failed units — named explicitly to avoid misreading.
    failed_batches: int


def _build_embedding_components(
    state: AppState,
    settings: Settings,
    runtime: RuntimeConfig | None = None,
) -> tuple[EmbeddingPipeline, Embedder] | None:
    """Construct the Phase-3 embedding stack, or None when disabled.

    Returned as a (pipeline, embedder) pair so callers can `aclose()` the
    embedder after the request (no-op for the local embedder, releases the
    HTTP client for the OpenAI one). Module-level function (not a closure)
    so tests can monkeypatch it with a fake pipeline.

    When `runtime` is supplied (the production path), the mode (local |
    openai), enable decision, key, and model are resolved from
    `RuntimeConfig` (Postgres-over-env) via the shared
    `build_runtime_embedder` — so a key/mode change takes effect on the
    next ingest without a restart, and the embedder matches the one the
    query side uses. When `runtime` is None (legacy / test path), it reads
    straight from `settings` (always OpenAI), the pre-onboarding behavior.
    """
    if runtime is not None:
        embedder = build_runtime_embedder(runtime)
        if embedder is None:
            return None
    else:
        if not settings.embeddings_enabled:
            return None
        assert settings.openai_api_key is not None  # guaranteed by enabled
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


class RepoPathError(ValueError):
    """`repo_path` does not point at a readable directory on the API host."""


@dataclass(frozen=True)
class IngestOutcome:
    """Result of one in-process ingest — what `run_ingest` returns to any
    caller (the HTTP endpoint, the freshness poller, the watcher)."""

    repo_id: str
    commit_sha: str
    units_collection: str
    metrics: dict[str, float | int]
    failed_files: list[str]


async def run_ingest(
    state: AppState,
    settings: Settings,
    runtime: RuntimeConfig | None,
    *,
    repo_id: str,
    repo_path: str,
    commit_sha: str,
) -> IngestOutcome:
    """Walk `repo_path`, parse it, and refresh the memory for `repo_id`.

    The single in-process ingestion path, shared by the `/ingest` endpoint
    and the Phase-3 freshness loops (poller + watcher) — so they can't
    drift apart. Sizes the Qdrant collection to the active embedding
    dimension, builds the embedding stack from `RuntimeConfig`, runs the
    pipeline, and always releases the embedder. Raises `RepoPathError`
    when the path isn't a directory; callers map that to a 400 (endpoint)
    or a registry error (freshness).
    """
    repo_root = Path(repo_path)
    # ASYNC240: a single-shot stat() is fine — anyio.Path would add a
    # dependency for one call.
    if not repo_root.exists() or not repo_root.is_dir():  # noqa: ASYNC240
        raise RepoPathError(f"repo_path is not a directory: {repo_root}")

    # Qdrant ≥1.11 rejects ":" in collection names with a 422. Use "_" as
    # the separator. The user-facing repo_id is unchanged; only the
    # storage-layer name is rewritten.
    collection = f"repo_{repo_id}"
    # Size the collection to the ACTIVE embedding dimension (384 local /
    # 1536 openai) so the vectors fit. Mismatched sizes get rejected by
    # Qdrant mid-upsert.
    vector_size = (
        active_embedding_dimension(runtime)
        if runtime is not None
        else _DEFAULT_VECTOR_SIZE
    )
    await state.vector_repo.ensure_collection(collection, vector_size)

    ctx = make_context(
        repo_id=repo_id,
        repo_path=repo_root,
        commit_sha=commit_sha,
        units_collection=collection,
        units_repo=state.units_repo,
        graph_repo=state.graph_repo,
        vector_repo=state.vector_repo,
    )
    components = _build_embedding_components(state, settings, runtime)
    try:
        result = await IngestionPipeline(
            embedding_pipeline=components[0] if components else None,
        ).run(ctx)
    finally:
        if components is not None:
            await components[1].aclose()
    return IngestOutcome(
        repo_id=result.repo_id,
        commit_sha=result.commit_sha,
        units_collection=collection,
        metrics=dict(result.metrics),
        failed_files=list(result.failed_files),
    )


@router.post(
    "",
    response_model=IngestResponse,
    status_code=status.HTTP_200_OK,
)
async def ingest_repo(
    req: IngestRequest,
    request: Request,
    state: AppStateDep,
    api_key: ApiKeyDep,  # auth enforced here, same dependency as /mcp/tools
    principal: SoftPrincipalDep,
) -> IngestResponse:
    """Trigger ingestion of `repo_path` under tenant `repo_id` at `commit_sha`.

    Idempotent — re-running the same (repo_id, commit_sha) on identical
    content is a no-op for unchanged units; per-file failures are isolated
    and reported via `failed_files`. When embeddings are enabled, changed
    units get real vectors in the same run. On success the repo is recorded
    in the freshness registry as a `local` repo (persisting `repo_path` so
    the watcher can keep it fresh).
    """
    # Per-repo RBAC: non-breaking (no-op) when auth is unconfigured or caller
    # is unauthenticated; otherwise requires write access on this repo.
    await assert_repo_access(request, principal, req.repo_id, "write", state)

    runtime = getattr(request.app.state, "runtime_config", None)
    try:
        outcome = await run_ingest(
            state,
            get_settings(),
            runtime,
            repo_id=req.repo_id,
            repo_path=req.repo_path,
            commit_sha=req.commit_sha,
        )
    except RepoPathError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # Register/refresh this repo in the freshness registry (local source).
    # Stamp org_id from the authenticated principal (fallback "default").
    # None-safe: test apps without the registry wired just skip it.
    org_id = principal.org_id if principal.is_authenticated else "default"
    registry = getattr(request.app.state, "repo_registry", None)
    if registry is not None:
        await registry.upsert_local(req.repo_id, req.repo_path, req.commit_sha, org_id=org_id)

    return IngestResponse(
        repo_id=outcome.repo_id,
        commit_sha=outcome.commit_sha,
        units_collection=outcome.units_collection,
        metrics=outcome.metrics,
        failed_files=outcome.failed_files,
    )


@router.post(
    "/reembed",
    response_model=ReembedResponse,
    status_code=status.HTTP_200_OK,
)
async def reembed_repo(
    req: ReembedRequest,
    request: Request,
    state: AppStateDep,
    api_key: ApiKeyDep,  # auth enforced here — reembed spends provider money
    principal: SoftPrincipalDep,
) -> ReembedResponse:
    """Backfill real vectors for every unit already ingested for `repo_id`.

    Use after configuring OPENAI_API_KEY on a deployment that ingested
    with placeholder vectors, or after an embed-degraded ingest. Batches
    are independent: a provider failure skips that batch (counted in
    `failed_batches`) and the run continues. Only one reembed per repo
    may be in flight at a time — concurrent requests get a 409.
    """
    # Per-repo RBAC: non-breaking (no-op) when auth is unconfigured or caller
    # is unauthenticated; otherwise requires write access on this repo.
    await assert_repo_access(request, principal, req.repo_id, "write", state)

    settings = get_settings()
    runtime = getattr(request.app.state, "runtime_config", None)
    embeddings_on = (
        runtime.embeddings_enabled() if runtime is not None
        else settings.embeddings_enabled
    )
    if not embeddings_on:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="embeddings are disabled — set OPENAI_API_KEY to enable reembed",
        )

    # No await between the membership check and the add, so this is
    # atomic within the event loop (single-process uvicorn).
    if req.repo_id in _REEMBED_IN_FLIGHT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"reembed already in progress for repo_id {req.repo_id!r}",
        )
    _REEMBED_IN_FLIGHT.add(req.repo_id)
    try:
        components = _build_embedding_components(state, settings, runtime)
        assert components is not None  # embeddings_enabled checked above
        pipeline, embedder = components

        units = list(await state.units_repo.list_units_for_repo(req.repo_id))
        collection = f"repo_{req.repo_id}"

        embedded = 0
        failed_batches = 0
        try:
            for i in range(0, len(units), _REEMBED_BATCH_SIZE):
                batch = units[i : i + _REEMBED_BATCH_SIZE]
                try:
                    await pipeline.run(batch, collection=collection)
                except Exception:
                    # Batch-level isolation: keep going, report the count.
                    failed_batches += 1
                else:
                    embedded += len(batch)
        finally:
            await embedder.aclose()
    finally:
        _REEMBED_IN_FLIGHT.discard(req.repo_id)

    return ReembedResponse(
        repo_id=req.repo_id,
        units_total=len(units),
        units_embedded=embedded,
        failed_batches=failed_batches,
    )
