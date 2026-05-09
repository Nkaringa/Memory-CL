"""`ingest_repository(path)` — orchestration wrapper for IngestionPipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.ingestion import IngestionPipeline, make_context
from core.mcp.execution.tool_executor import ExecutionContext, Tool  # noqa: F401
from core.mcp.schemas import IngestRepositoryRequest


class IngestRepositoryTool:
    name: str = "ingest_repository"
    request_schema = IngestRepositoryRequest

    async def execute(
        self, request: IngestRepositoryRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        repo_root = Path(request.path)
        # ASYNC240: a single stat() at request entry is fine — the
        # alternative (anyio.Path) adds a dep for one call.
        if not repo_root.exists() or not repo_root.is_dir():  # noqa: ASYNC240
            raise FileNotFoundError(f"repo path does not exist: {repo_root}")

        # The Phase-2 vector collection bootstrap requires a dimension;
        # at the MCP boundary we don't have access to a real embedder
        # config beyond what AppState exposes, so we read it from there.
        embedding_dim = getattr(ctx.state.embedder, "dimension", 1536)
        collection = f"repo:{request.repo_id}"
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
        result = await IngestionPipeline().run(ic)

        return {
            "repo_id": result.repo_id,
            "commit_sha": result.commit_sha,
            "units_collection": collection,
            "metrics": dict(result.metrics),
            "failed_files": list(result.failed_files),
        }


__all__ = ["IngestRepositoryTool"]
