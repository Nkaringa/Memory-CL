"""`ingest_repository(path)` — orchestration wrapper for IngestionPipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.config import get_settings
from core.embeddings import ChunkingStrategy, EmbeddingPipeline, OpenAIEmbedder
from core.ingestion import IngestionPipeline, make_context
from core.mcp.execution.tool_executor import ExecutionContext, Tool  # noqa: F401
from core.mcp.schemas import IngestRepositoryRequest

# Matches apps/api/routers/ingest.py: OpenAI small / Voyage default.
_DEFAULT_VECTOR_SIZE = 1536


def _build_embedding_components(
    vector_repo: Any,
) -> tuple[EmbeddingPipeline, OpenAIEmbedder] | None:
    """Construct the Phase-3 embedding stack, or None when disabled.

    Mirrors `apps.api.routers.ingest._build_embedding_components` so an
    MCP-triggered ingest behaves exactly like an HTTP-triggered one:
    changed units get real vectors in the same run. Settings come from
    the process-wide `get_settings()` (the MCP boundary's AppState does
    not carry Settings). Module-level function so tests can monkeypatch
    it with a fake pipeline.
    """
    settings = get_settings()
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
        vector_repo=vector_repo,
    )
    return pipeline, embedder


class IngestRepositoryTool:
    name: str = "ingest_repository"
    description: str = (
        "MUTATES STATE — parses a repository from the server's local "
        "filesystem and writes units, graph nodes/edges, and vectors "
        "into all three stores, e.g. ingest_repository(path='/srv/repos/"
        "myrepo', repo_id='myrepo'). Slow on large repos and re-ingests "
        "overwrite changed units. Only call when the user explicitly "
        "asks to ingest/re-ingest; never as part of answering a "
        "question. `path` must exist on the SERVER host, not your "
        "machine."
    )
    request_schema = IngestRepositoryRequest

    async def execute(
        self, request: IngestRepositoryRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        repo_root = Path(request.path)
        # ASYNC240: a single stat() at request entry is fine — the
        # alternative (anyio.Path) adds a dep for one call.
        if not repo_root.exists() or not repo_root.is_dir():  # noqa: ASYNC240
            raise FileNotFoundError(f"repo path does not exist: {repo_root}")

        # Phase 3: wire the embedding stack the same way the HTTP router
        # does, reading Settings from the process environment. When
        # embeddings are disabled this is None and the pipeline keeps
        # its placeholder-only behavior. Either way the IngestionPipeline
        # guarantees an embedding-enabled re-ingest never rewrites
        # unchanged units' points (which would wipe their real vectors).
        components = _build_embedding_components(ctx.state.vector_repo)

        # The Phase-2 vector collection bootstrap requires a dimension.
        # With embeddings enabled, use the real embedder's dimension;
        # otherwise fall back to what AppState exposes.
        embedding_dim = (
            components[1].dimension
            if components is not None
            else getattr(ctx.state.embedder, "dimension", _DEFAULT_VECTOR_SIZE)
        )
        # See apps/api/routers/ingest.py for the rationale: Qdrant ≥1.11
        # rejects ":" in collection names. Use "_" as separator.
        collection = f"repo_{request.repo_id}"
        await ctx.state.vector_repo.ensure_collection(collection, embedding_dim)

        ic = make_context(
            repo_id=request.repo_id,
            repo_path=repo_root,
            commit_sha=request.commit_sha,
            units_collection=collection,
            units_repo=ctx.state.units_repo,
            graph_repo=ctx.state.graph_repo,
            vector_repo=ctx.state.vector_repo,
        )
        try:
            result = await IngestionPipeline(
                embedding_pipeline=components[0] if components else None,
            ).run(ic)
        finally:
            if components is not None:
                await components[1].aclose()

        return {
            "repo_id": result.repo_id,
            "commit_sha": result.commit_sha,
            "units_collection": collection,
            "metrics": dict(result.metrics),
            "failed_files": list(result.failed_files),
        }


__all__ = ["IngestRepositoryTool"]
