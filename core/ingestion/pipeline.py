from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from core.ingestion.context import IngestionContext, IngestionMetrics
from core.ingestion.graph_builder import _UNIT_TO_NODE, GraphBuilder
from core.ingestion.logevent import emit_phase2_event
from core.observability import get_tracer
from core.parsing import FileWalker, PythonParser
from schemas import FileRef, IngestionUnit, NodeKind
from storage.repositories import VectorPoint

_tracer = get_tracer("core.ingestion.pipeline")


@dataclass(slots=True)
class IngestionResult:
    """Compact, deterministic per-run summary returned to API callers."""

    repo_id: str
    commit_sha: str
    metrics: dict[str, float | int]
    failed_files: tuple[str, ...]


class IngestionPipeline:
    """Orchestrate file walk → parse → graph build → multi-store write.

    Failure isolation: a parse error or single-file write failure marks
    that file as failed and the pipeline keeps going. The pipeline is
    resumable — re-running on the same commit replays the exact same
    deterministic operations and is a no-op for unchanged files.
    """

    def __init__(
        self,
        *,
        walker: FileWalker | None = None,
        parser: PythonParser | None = None,
        builder: GraphBuilder | None = None,
    ) -> None:
        self._walker = walker or FileWalker()
        self._parser = parser or PythonParser()
        self._builder = builder or GraphBuilder()

    async def run(self, ctx: IngestionContext) -> IngestionResult:
        run_start = time.perf_counter()
        with _tracer.start_as_current_span("ingestion.run") as span:
            span.set_attribute("repo_id", ctx.repo_id)
            span.set_attribute("commit_sha", ctx.commit_sha)
            span.set_attribute("repo_path", str(ctx.repo_path))

            emit_phase2_event(
                event="pipeline_start",
                operation="ingestion.run",
                status="success",
                duration_ms=0.0,
                level="info",
                repo_id=ctx.repo_id,
                commit_sha=ctx.commit_sha,
            )

            walk = self._walker.walk(ctx.repo_path, repo_id=ctx.repo_id)
            ctx.metrics.files_walked = len(walk.files)

            # ---- Pass 1: parse every file, build the global qname index.
            file_units, failed_files = await self._parse_all(walk.files, ctx)
            qname_resolver = self._build_qname_resolver(file_units)

            # ---- Pass 2: per-file graph + write.
            for file_ref, units in file_units.items():
                try:
                    await self._ingest_file(file_ref, units, qname_resolver, ctx)
                except Exception as exc:
                    ctx.metrics.files_failed += 1
                    failed_files.append(file_ref.path)
                    emit_phase2_event(
                        event="ingest_file_failed",
                        operation="ingestion.ingest_file",
                        status="failed",
                        duration_ms=0.0,
                        file_path=file_ref.path,
                        level="error",
                        error=str(exc),
                    )

            ctx.metrics.duration_ms = (time.perf_counter() - run_start) * 1000
            metrics_payload = {
                k: v for k, v in ctx.metrics.as_dict().items() if k != "duration_ms"
            }
            emit_phase2_event(
                event="pipeline_end",
                operation="ingestion.run",
                status="partial" if failed_files else "success",
                duration_ms=ctx.metrics.duration_ms,
                level="info",
                **metrics_payload,
            )
            return IngestionResult(
                repo_id=ctx.repo_id,
                commit_sha=ctx.commit_sha,
                metrics=ctx.metrics.as_dict(),
                failed_files=tuple(sorted(set(failed_files))),
            )

    # ----- Pass 1 helpers -----
    async def _parse_all(
        self,
        files: Iterable[FileRef],
        ctx: IngestionContext,
    ) -> tuple[dict[FileRef, list[IngestionUnit]], list[str]]:
        out: dict[FileRef, list[IngestionUnit]] = {}
        failed: list[str] = []
        for file_ref in files:
            full_path = ctx.repo_path / file_ref.path
            try:
                source = full_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                ctx.metrics.files_failed += 1
                failed.append(file_ref.path)
                emit_phase2_event(
                    event="file_read_failed",
                    operation="ingestion.read_file",
                    status="failed",
                    duration_ms=0.0,
                    file_path=file_ref.path,
                    level="error",
                    error=str(exc),
                )
                continue
            try:
                units = self._parser.parse_file(
                    source=source,
                    repo_id=ctx.repo_id,
                    file_path=file_ref.path,
                    commit_sha=ctx.commit_sha,
                )
            except SyntaxError:
                ctx.metrics.files_failed += 1
                failed.append(file_ref.path)
                # parse_file already emitted an error event.
                continue
            out[file_ref] = units
            ctx.metrics.files_parsed += 1
            ctx.metrics.units_emitted += len(units)
        return out, failed

    def _build_qname_resolver(
        self,
        file_units: dict[FileRef, list[IngestionUnit]],
    ) -> dict[str, tuple[str, NodeKind]]:
        resolver: dict[str, tuple[str, NodeKind]] = {}
        for units in file_units.values():
            for u in units:
                resolver[u.qualified_name] = (u.unit_id, _UNIT_TO_NODE[u.kind])
        return resolver

    # ----- Pass 2 helpers -----
    async def _ingest_file(
        self,
        file_ref: FileRef,
        units: list[IngestionUnit],
        resolver: dict[str, tuple[str, NodeKind]],
        ctx: IngestionContext,
    ) -> None:
        start = time.perf_counter()
        with _tracer.start_as_current_span("ingestion.ingest_file") as span:
            span.set_attribute("repo_id", ctx.repo_id)
            span.set_attribute("file_path", file_ref.path)
            span.set_attribute("unit_count", len(units))

            # Reconcile: drop rows whose unit_id no longer appears in the
            # current parse output. Done BEFORE writes so subsequent
            # upserts can recreate any survivors that moved within file.
            new_ids = {u.unit_id for u in units}
            existing = await ctx.units_repo.list_units_for_file(
                ctx.repo_id, file_ref.path
            )
            obsolete = [u for u in existing if u.unit_id not in new_ids]
            if obsolete:
                # The protocol exposes delete-per-file only, so we wipe
                # the file's footprint in all three stores and let the
                # upserts below re-create the surviving units. Phase 2
                # accepts the duplicate write — a per-id delete path is
                # a Phase 4 optimisation.
                await ctx.units_repo.delete_units_for_file(ctx.repo_id, file_ref.path)
                await ctx.graph_repo.delete_subgraph_for_file(ctx.repo_id, file_ref.path)
                await ctx.vector_repo.delete_points_for_file(
                    ctx.units_collection, ctx.repo_id, file_ref.path
                )

            # Postgres
            changed = await ctx.units_repo.upsert_units(units)
            ctx.metrics.units_changed += changed

            # Neo4j: build nodes/edges then write
            graph = self._builder.build(units, qname_resolver=resolver)
            n_nodes = await ctx.graph_repo.upsert_nodes(graph.nodes)
            n_edges = await ctx.graph_repo.upsert_edges(graph.edges)
            ctx.metrics.nodes_written += n_nodes
            ctx.metrics.edges_written += n_edges

            # Qdrant payloads (no vectors yet)
            points = [
                VectorPoint(
                    point_id=u.unit_id,
                    repo_id=u.repo_id,
                    qualified_name=u.qualified_name,
                    kind=u.kind.value,
                    file_path=u.file_path,
                    line_start=u.line_start,
                    line_end=u.line_end,
                    commit_sha=u.commit_sha,
                    source_sha=u.source_sha,
                )
                for u in units
            ]
            n_vecs = await ctx.vector_repo.upsert_payloads(ctx.units_collection, points)
            ctx.metrics.vector_payloads_written += n_vecs

            emit_phase2_event(
                event="ingest_file_ok",
                operation="ingestion.ingest_file",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                file_path=file_ref.path,
                content_hash=units[0].source_sha if units else "",
                level="debug",
                units=len(units),
                changed=changed,
                nodes=n_nodes,
                edges=n_edges,
            )


def make_context(
    *,
    repo_id: str,
    repo_path: str | Path,
    commit_sha: str,
    units_collection: str,
    units_repo,
    graph_repo,
    vector_repo,
) -> IngestionContext:
    """Convenience factory used by the API layer.

    Keeping this constructor in `core/` ensures the API layer doesn't
    need to know which concrete repos exist — it only knows the
    `IngestionContext` interface.
    """
    return IngestionContext(
        repo_id=repo_id,
        repo_path=Path(repo_path).resolve(),
        commit_sha=commit_sha,
        units_collection=units_collection,
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
        metrics=IngestionMetrics(),
    )
