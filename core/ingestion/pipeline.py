from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.ingestion.context import IngestionContext, IngestionMetrics
from core.ingestion.graph_builder import _UNIT_TO_NODE, GraphBuilder
from core.ingestion.logevent import emit_phase2_event
from core.observability import get_tracer
from core.parsing import (
    DocParser,
    FileWalker,
    PythonParser,
    SourceParser,
    TreeSitterParser,
)
from schemas import (
    FileRef,
    GraphEdge,
    IngestionUnit,
    Language,
    NodeKind,
    UnitKind,
    stable_unit_id,
)
from storage.repositories import VectorPoint

if TYPE_CHECKING:
    # Runtime import would be circular: core.embeddings pulls in
    # core.compression, which reaches storage and back into
    # core.ingestion. The pipeline only needs the name for typing.
    from core.embeddings import EmbeddingPipeline

_tracer = get_tracer("core.ingestion.pipeline")


@dataclass(slots=True)
class IngestionResult:
    """Compact, deterministic per-run summary returned to API callers."""

    repo_id: str
    commit_sha: str
    metrics: dict[str, float | int]
    failed_files: tuple[str, ...]


def _resolve_qname_collisions(
    units: list[IngestionUnit],
) -> tuple[list[IngestionUnit], int]:
    """Disambiguate units within a file that share a qualified_name.

    Overloads (e.g. C# constructors) legally share a qualified name, but
    `unit_id = stable_unit_id(repo, path, qname)` is the primary key in
    every store — same qname means same id, so last-write-wins dropped
    all but one overload AND broke idempotency (the survivor flip-flopped
    between runs, wasting re-embeds).

    Suffix convention: the FIRST collider (by line order) keeps its
    qualified_name verbatim; each subsequent collider gets a line-order
    ordinal suffix `#2`, `#3`, … (e.g. `…SystemRandom.SystemRandom#2`)
    and its unit_id is recomputed over the suffixed qname. The graph and
    UI simply see distinct units under the suffixed names.

    If a CLASS qname collides (rare), the suffixed class's children —
    identified by parent_qualified_name match within the class's line
    span — get their parent_qualified_name remapped to the suffixed
    qname so DEFINES edges attach to the right parent.

    Returns the (possibly rewritten) unit list and the collision count.
    Deterministic: same input always yields the same suffixes and ids.
    """
    groups: dict[str, list[int]] = {}
    for i, u in enumerate(units):
        groups.setdefault(u.qualified_name, []).append(i)
    colliding = {q: idxs for q, idxs in groups.items() if len(idxs) > 1}
    if not colliding:
        return units, 0

    out = list(units)
    collisions = 0
    # (old_parent_qname, line_start, line_end, new_parent_qname)
    parent_remaps: list[tuple[str, int, int, str]] = []
    # Build the full set of qnames already present so we can skip any
    # candidate that would itself collide (e.g. input has [X, X, X#2]).
    all_qnames: set[str] = {u.qualified_name for u in units}
    for qname, idxs in colliding.items():
        ordered = sorted(idxs, key=lambda i: (units[i].line_start, units[i].line_end, i))
        for ordinal, i in enumerate(ordered[1:], start=2):
            u = units[i]
            # Fixpoint: bump the ordinal until the candidate is free.
            candidate = f"{qname}#{ordinal}"
            while candidate in all_qnames:
                ordinal += 1
                candidate = f"{qname}#{ordinal}"
            new_qname = candidate
            all_qnames.add(new_qname)
            out[i] = u.model_copy(
                update={
                    "qualified_name": new_qname,
                    "unit_id": stable_unit_id(u.repo_id, u.file_path, new_qname),
                }
            )
            collisions += 1
            if u.kind == UnitKind.CLASS:
                parent_remaps.append((qname, u.line_start, u.line_end, new_qname))

    for old_q, line_start, line_end, new_q in parent_remaps:
        for i, u in enumerate(out):
            if (
                u.parent_qualified_name == old_q
                and u.qualified_name != new_q  # never remap the class itself
                and line_start <= u.line_start
                and u.line_end <= line_end
            ):
                out[i] = u.model_copy(update={"parent_qualified_name": new_q})
    return out, collisions


def _default_parsers() -> dict[Language, SourceParser]:
    return {
        Language.PYTHON: PythonParser(),
        Language.JAVASCRIPT: TreeSitterParser(Language.JAVASCRIPT),
        Language.TYPESCRIPT: TreeSitterParser(Language.TYPESCRIPT),
        Language.CSHARP: TreeSitterParser(Language.CSHARP),
        Language.GO: TreeSitterParser(Language.GO),
        Language.JAVA: TreeSitterParser(Language.JAVA),
        Language.RUST: TreeSitterParser(Language.RUST),
        Language.MARKDOWN: DocParser(Language.MARKDOWN),
        Language.TEXT: DocParser(Language.TEXT),
    }


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
        parsers: dict[Language, SourceParser] | None = None,
        builder: GraphBuilder | None = None,
        embedding_pipeline: EmbeddingPipeline | None = None,
    ) -> None:
        self._walker = walker or FileWalker()
        self._parsers = parsers if parsers is not None else _default_parsers()
        self._builder = builder or GraphBuilder()
        # Phase 3: when present, changed units get real vectors right
        # after the placeholder payload write. None preserves the
        # pre-Phase-3 placeholder-only behavior.
        self._embedding_pipeline = embedding_pipeline

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

            # ---- Pass 2: per-file reconciliation + node/vector writes.
            # Edges are NOT written here. Files ingest in alphabetical
            # order, so a per-file edge write whose destination lives in
            # a later file would MATCH nothing and be silently dropped
            # (forward cross-file edges), and reconciliation's DETACH
            # DELETE severs inbound edges from unchanged files that a
            # per-file pass would never rewrite. Instead each file's
            # edges are collected run-wide and upserted in a FINAL pass
            # once every surviving file's nodes exist.
            collected_edges: dict[tuple[str, str, str], GraphEdge] = {}
            for file_ref, units in file_units.items():
                try:
                    file_edges = await self._ingest_file(
                        file_ref, units, qname_resolver, ctx
                    )
                except Exception as exc:
                    # Failure isolation: this file's edges are simply
                    # absent from the final edge set. Edges from OTHER
                    # files pointing into it may miss their endpoint in
                    # the final pass — upsert_edges counts and reports
                    # those as dropped rather than failing the run.
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
                else:
                    for e in file_edges:
                        collected_edges.setdefault(
                            (e.src_id, e.kind.value, e.dst_id), e
                        )

            # ---- Final pass: run-wide edge upsert (always runs, even
            # when some files failed — the survivors' edges still land).
            await self._write_edges(collected_edges, ctx)

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
            parser = self._parsers.get(file_ref.language)
            if parser is None:
                # Walked but no parser registered — skip silently, same
                # behavior as unknown extensions at the walker level.
                continue
            try:
                units = parser.parse_file(
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
            # Language-agnostic overload disambiguation BEFORE any id is
            # used as a storage key (see _resolve_qname_collisions).
            units, collisions = _resolve_qname_collisions(units)
            if collisions:
                emit_phase2_event(
                    event="qname_collisions_resolved",
                    operation="ingestion.parse_file",
                    status="success",
                    duration_ms=0.0,
                    file_path=file_ref.path,
                    level="debug",
                    collisions=collisions,
                )
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
    ) -> tuple[GraphEdge, ...]:
        """Reconcile + write one file's units to Postgres/Neo4j/Qdrant.

        Nodes and vector payloads are written here; EDGES are returned
        to the caller, which upserts the whole run's edge set in a final
        pass after every file's nodes exist (see `run`).
        """
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
            existing_sha = {u.unit_id: u.source_sha for u in existing}
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

            # Neo4j: build nodes + edges; write NODES only. Edges are
            # returned for the run-wide final pass (forward cross-file
            # targets don't exist yet at this point of the loop).
            graph = self._builder.build(units, qname_resolver=resolver)
            n_nodes = await ctx.graph_repo.upsert_nodes(graph.nodes)
            ctx.metrics.nodes_written += n_nodes

            # Phase 3: a unit is "changed" when its id is new OR its
            # source_sha differs from the row we just reconciled against.
            # When the obsolete path wiped the file's vector footprint
            # above, every surviving unit lost its vector with it — they
            # all need fresh points and re-embeds.
            if obsolete:
                changed_units = list(units)
            else:
                changed_units = [
                    u
                    for u in units
                    if existing_sha.get(u.unit_id) != u.source_sha
                ]

            # Qdrant payloads (no vectors yet). Qdrant upsert replaces
            # whole points, so with an embedding pipeline wired we write
            # placeholders ONLY for changed units — unchanged units'
            # points already exist with real vectors and rewriting them
            # would zero those vectors out (data loss). Without an
            # embedding pipeline every point is a placeholder anyway, so
            # the all-units write is harmless and keeps payload metadata
            # fresh. (The wipe path above is covered either way:
            # changed_units == all units of the file there.)
            placeholder_units = (
                changed_units if self._embedding_pipeline is not None else units
            )
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
                for u in placeholder_units
            ]
            n_vecs = 0
            if points:
                n_vecs = await ctx.vector_repo.upsert_payloads(
                    ctx.units_collection, points
                )
            ctx.metrics.vector_payloads_written += n_vecs

            if self._embedding_pipeline is not None and changed_units:
                embed_start = time.perf_counter()
                try:
                    await self._embedding_pipeline.run(
                        changed_units, collection=ctx.units_collection
                    )
                except Exception as exc:
                    # Never fail ingest on embedding errors — the
                    # placeholder points written above stay in place and
                    # a later `/ingest/reembed` can backfill.
                    emit_phase2_event(
                        event="embed_failed",
                        operation="ingestion.embed_units",
                        status="degraded",
                        duration_ms=(time.perf_counter() - embed_start) * 1000,
                        file_path=file_ref.path,
                        level="warning",
                        units=len(changed_units),
                        error=str(exc),
                    )
                else:
                    ctx.metrics.units_embedded += len(changed_units)

            # No `edges` field here on purpose: edges are written once,
            # run-wide, after the per-file loop — see `edge_pass_ok`.
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
            )
            return graph.edges

    async def _write_edges(
        self,
        collected: dict[tuple[str, str, str], GraphEdge],
        ctx: IngestionContext,
    ) -> None:
        """Final run-wide edge pass.

        Runs after every surviving file's nodes are upserted, so forward
        cross-file edges resolve and inbound edges severed by the
        reconciliation wipe are rewritten (the full run's edge set
        includes the unchanged files' edges).

        `ctx.metrics.edges_written` is counted ONCE here, from the graph
        repo's honest written count — not per file, not len(edges).

        A total edge-pass failure must NOT zero the whole ingest: units,
        nodes and vector payloads are already durable, so we emit a
        degraded `edge_pass_failed` event and keep the run alive — the
        next ingest of the same commit replays the full edge set.
        """
        edges = sorted(
            collected.values(), key=lambda e: (e.kind.value, e.src_id, e.dst_id)
        )
        if not edges:
            return
        start = time.perf_counter()
        try:
            written = await ctx.graph_repo.upsert_edges(edges)
        except Exception as exc:
            emit_phase2_event(
                event="edge_pass_failed",
                operation="ingestion.write_edges",
                status="degraded",
                duration_ms=(time.perf_counter() - start) * 1000,
                level="warning",
                edges=len(edges),
                error=str(exc),
            )
            return
        ctx.metrics.edges_written += written
        emit_phase2_event(
            event="edge_pass_ok",
            operation="ingestion.write_edges",
            status="success",
            duration_ms=(time.perf_counter() - start) * 1000,
            level="debug",
            edges=len(edges),
            written=written,
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
