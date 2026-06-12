from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from core.ingestion.logevent import emit_phase2_event
from core.observability import get_tracer
from schemas import (
    EdgeKind,
    GraphEdge,
    GraphNode,
    IngestionUnit,
    NodeKind,
    UnitKind,
    is_edge_allowed,
)

_tracer = get_tracer("core.ingestion.graph_builder")


# Maps the parser's UnitKind to a Neo4j NodeKind.
_UNIT_TO_NODE: dict[UnitKind, NodeKind] = {
    UnitKind.MODULE: NodeKind.MODULE,
    UnitKind.CLASS: NodeKind.CLASS,
    UnitKind.FUNCTION: NodeKind.FUNCTION,
    UnitKind.METHOD: NodeKind.METHOD,
    UnitKind.CONSTANT: NodeKind.CONSTANT,
    UnitKind.SECTION: NodeKind.SECTION,
}

# qname -> (unit_id, kind). The pipeline builds this once per ingest
# run by walking every parsed file before invoking GraphBuilder, so
# cross-file calls/imports/bases can resolve to real units.
QnameResolver = dict[str, tuple[str, NodeKind]]


def _file_node_id(repo_id: str, file_path: str) -> str:
    return f"file:{repo_id}:{file_path}"


def _external_id(qualified_name: str) -> str:
    return f"external:{qualified_name}"


_SOURCE_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".jsx", ".mjs", ".cjs", ".js",
    ".tsx", ".mts", ".cts", ".ts",
    ".cs", ".go", ".java", ".rs",
    ".mdx", ".md", ".rst", ".txt",
)

_INDEX_COLLAPSE_SUFFIXES: frozenset[str | None] = frozenset({
    ".jsx", ".mjs", ".cjs", ".js",
    ".tsx", ".mts", ".cts", ".ts",
    None,
})


def _module_qname(file_path: str) -> str:
    """Mirror of `core.parsing.qnames.module_qname_from_path`.

    Inlined to keep this module's import surface narrow — graph_builder
    already depends on parsing semantically; pulling the function in
    would create a redundant runtime dependency edge. Kept in sync by
    tests/test_qnames.py::test_graph_builder_mirror_stays_in_sync.
    """
    matched: str | None = None
    stem_path = file_path
    for suffix in _SOURCE_SUFFIXES:
        if file_path.endswith(suffix):
            stem_path = file_path[: -len(suffix)]
            matched = suffix
            break
    parts = stem_path.split("/")
    if len(parts) > 1 and (
        (matched == ".py" and parts[-1] == "__init__")
        or (matched == ".rs" and parts[-1] == "mod")
        or (matched in _INDEX_COLLAPSE_SUFFIXES and parts[-1] == "index")
    ):
        parts = parts[:-1]
    return ".".join(parts)


@dataclass(frozen=True, slots=True)
class GraphBuildResult:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class EdgeRuleViolation(RuntimeError):
    """Raised when GraphBuilder produces an edge that EDGE_RULES forbids.

    Per EXECUTION_CONSTRAINTS this is fail-fast: a programmer error in
    the builder, not a recoverable parse hiccup.
    """


class GraphBuilder:
    """Convert a per-file batch of `IngestionUnit`s into nodes + edges.

    The builder operates on a single batch (typically the units produced
    from one file). Cross-file resolution is best-effort: if a callee or
    import target matches a unit qname inside the same batch we link to
    it; otherwise we materialize an `External` node so retrieval can
    still surface the relationship.

    Determinism: result nodes/edges are sorted by stable keys before
    return — same input → byte-identical wire output.
    """

    def build(
        self,
        units: Sequence[IngestionUnit],
        *,
        qname_resolver: QnameResolver | None = None,
    ) -> GraphBuildResult:
        """Build nodes + edges from `units`.

        Parameters
        ----------
        units:
            Units from a single file (or one ingestion batch).
        qname_resolver:
            Optional `qname -> (unit_id, NodeKind)` map for cross-file
            resolution. When provided, calls/imports/bases referencing
            those qnames link to the real units instead of External
            placeholders. When None, only the qnames inside `units`
            itself are resolved; everything else is External.
        """
        start = time.perf_counter()
        with _tracer.start_as_current_span("graph_builder.build") as span:
            # Always seed the resolver with this batch's units; caller
            # entries layer on top so cross-file truths win.
            local_resolver: QnameResolver = {
                u.qualified_name: (u.unit_id, _UNIT_TO_NODE[u.kind]) for u in units
            }
            if qname_resolver:
                local_resolver = {**local_resolver, **qname_resolver}

            nodes: dict[str, GraphNode] = {}
            edges: list[GraphEdge] = []
            # Edge-validation kind lookup combines in-batch nodes with
            # cross-batch entries from the resolver.
            kind_lookup: dict[str, NodeKind] = {}

            self._add_file_nodes(units, nodes, kind_lookup)
            self._add_unit_nodes(units, nodes, kind_lookup)
            self._add_structural_edges(units, edges)
            self._add_import_edges(units, local_resolver, nodes, kind_lookup, edges)
            self._add_call_edges(units, local_resolver, nodes, kind_lookup, edges)
            self._add_inherits_edges(units, local_resolver, nodes, kind_lookup, edges)

            # Validate every edge against EDGE_RULES — fail-fast on bug.
            for e in edges:
                src_kind = kind_lookup.get(e.src_id)
                dst_kind = kind_lookup.get(e.dst_id)
                if src_kind is None or dst_kind is None:
                    raise EdgeRuleViolation(
                        f"edge {e.src_id}-[{e.kind.value}]->{e.dst_id} references "
                        f"a node whose kind is unknown to the builder"
                    )
                if not is_edge_allowed(src_kind, e.kind, dst_kind):
                    raise EdgeRuleViolation(
                        f"edge {src_kind.value}-[{e.kind.value}]->{dst_kind.value} "
                        f"forbidden by EDGE_RULES"
                    )

            sorted_nodes = tuple(
                sorted(nodes.values(), key=lambda n: (n.kind.value, n.node_id))
            )
            # Edges deduplicated by natural merge key, then sorted.
            uniq: dict[tuple[str, str, str], GraphEdge] = {}
            for e in edges:
                uniq[(e.src_id, e.kind.value, e.dst_id)] = e
            sorted_edges = tuple(
                sorted(uniq.values(), key=lambda e: (e.kind.value, e.src_id, e.dst_id))
            )

            duration = (time.perf_counter() - start) * 1000
            span.set_attribute("nodes", len(sorted_nodes))
            span.set_attribute("edges", len(sorted_edges))
            emit_phase2_event(
                event="graph_build_ok",
                operation="graph_builder.build",
                status="success",
                duration_ms=duration,
                nodes=len(sorted_nodes),
                edges=len(sorted_edges),
                level="debug",
            )
            return GraphBuildResult(nodes=sorted_nodes, edges=sorted_edges)

    # ----- internals -----
    def _add_file_nodes(
        self,
        units: Sequence[IngestionUnit],
        nodes: dict[str, GraphNode],
        kind_lookup: dict[str, NodeKind],
    ) -> None:
        seen: set[tuple[str, str]] = set()
        for u in units:
            key = (u.repo_id, u.file_path)
            if key in seen:
                continue
            seen.add(key)
            node_id = _file_node_id(u.repo_id, u.file_path)
            nodes[node_id] = GraphNode(
                node_id=node_id,
                kind=NodeKind.FILE,
                repo_id=u.repo_id,
                qualified_name=u.file_path,
                name=u.file_path.rsplit("/", 1)[-1],
                file_path=u.file_path,
                commit_sha=u.commit_sha,
            )
            kind_lookup[node_id] = NodeKind.FILE

    def _add_unit_nodes(
        self,
        units: Sequence[IngestionUnit],
        nodes: dict[str, GraphNode],
        kind_lookup: dict[str, NodeKind],
    ) -> None:
        for u in units:
            kind = _UNIT_TO_NODE[u.kind]
            nodes[u.unit_id] = GraphNode(
                node_id=u.unit_id,
                kind=kind,
                repo_id=u.repo_id,
                qualified_name=u.qualified_name,
                name=u.name,
                file_path=u.file_path,
                line_start=u.line_start,
                line_end=u.line_end,
                commit_sha=u.commit_sha,
                source_sha=u.source_sha,
            )
            kind_lookup[u.unit_id] = kind

    def _add_structural_edges(
        self,
        units: Sequence[IngestionUnit],
        edges: list[GraphEdge],
    ) -> None:
        qname_to_id = {u.qualified_name: u.unit_id for u in units}
        for u in units:
            file_id = _file_node_id(u.repo_id, u.file_path)

            # File CONTAINS every non-module unit. Module is the file's
            # primary content; using DEFINES from module to children keeps
            # the graph faithful to Python semantics.
            if u.kind != UnitKind.MODULE:
                edges.append(
                    GraphEdge(
                        src_id=file_id,
                        kind=EdgeKind.CONTAINS,
                        dst_id=u.unit_id,
                        repo_id=u.repo_id,
                        commit_sha=u.commit_sha,
                    )
                )

            # DEFINES from parent (module or class) to child unit.
            if u.parent_qualified_name and u.parent_qualified_name in qname_to_id:
                parent_id = qname_to_id[u.parent_qualified_name]
                edges.append(
                    GraphEdge(
                        src_id=parent_id,
                        kind=EdgeKind.DEFINES,
                        dst_id=u.unit_id,
                        repo_id=u.repo_id,
                        commit_sha=u.commit_sha,
                    )
                )

    def _add_import_edges(
        self,
        units: Sequence[IngestionUnit],
        resolver: QnameResolver,
        nodes: dict[str, GraphNode],
        kind_lookup: dict[str, NodeKind],
        edges: list[GraphEdge],
    ) -> None:
        for u in units:
            # Sections carry doc-link imports (docs ingestion) — same
            # resolution rules, allowed by the Section-IMPORTS edge rule.
            if u.kind not in (UnitKind.MODULE, UnitKind.SECTION) or not u.imports:
                continue
            for imp in u.imports:
                target_id = self._resolve_or_external(
                    qname=imp,
                    candidates=(imp,),
                    resolver=resolver,
                    nodes=nodes,
                    kind_lookup=kind_lookup,
                    repo_id=u.repo_id,
                    allowed_kinds={NodeKind.MODULE},
                )
                if target_id == u.unit_id:
                    # A doc linking to itself — self-edges are illegal.
                    continue
                edges.append(
                    GraphEdge(
                        src_id=u.unit_id,
                        kind=EdgeKind.IMPORTS,
                        dst_id=target_id,
                        repo_id=u.repo_id,
                        commit_sha=u.commit_sha,
                    )
                )

    def _add_call_edges(
        self,
        units: Sequence[IngestionUnit],
        resolver: QnameResolver,
        nodes: dict[str, GraphNode],
        kind_lookup: dict[str, NodeKind],
        edges: list[GraphEdge],
    ) -> None:
        for u in units:
            if u.kind not in (UnitKind.FUNCTION, UnitKind.METHOD) or not u.calls:
                continue
            module_qname = _module_qname(u.file_path)
            for callee in u.calls:
                # Try the bare callee, then `<module>.callee` (covers a
                # function calling another function in the same file).
                candidates = (callee, f"{module_qname}.{callee}") if "." not in callee else (callee,)
                target_id = self._resolve_or_external(
                    qname=callee,
                    candidates=candidates,
                    resolver=resolver,
                    nodes=nodes,
                    kind_lookup=kind_lookup,
                    repo_id=u.repo_id,
                    allowed_kinds={NodeKind.FUNCTION, NodeKind.METHOD},
                )
                if target_id == u.unit_id:
                    # Self-edges are illegal by GraphEdge validator.
                    continue
                edges.append(
                    GraphEdge(
                        src_id=u.unit_id,
                        kind=EdgeKind.CALLS,
                        dst_id=target_id,
                        repo_id=u.repo_id,
                        commit_sha=u.commit_sha,
                    )
                )

    def _add_inherits_edges(
        self,
        units: Sequence[IngestionUnit],
        resolver: QnameResolver,
        nodes: dict[str, GraphNode],
        kind_lookup: dict[str, NodeKind],
        edges: list[GraphEdge],
    ) -> None:
        for u in units:
            if u.kind != UnitKind.CLASS or not u.bases:
                continue
            module_qname = _module_qname(u.file_path)
            for base in u.bases:
                candidates = (base, f"{module_qname}.{base}") if "." not in base else (base,)
                target_id = self._resolve_or_external(
                    qname=base,
                    candidates=candidates,
                    resolver=resolver,
                    nodes=nodes,
                    kind_lookup=kind_lookup,
                    repo_id=u.repo_id,
                    allowed_kinds={NodeKind.CLASS},
                )
                if target_id == u.unit_id:
                    continue
                edges.append(
                    GraphEdge(
                        src_id=u.unit_id,
                        kind=EdgeKind.INHERITS,
                        dst_id=target_id,
                        repo_id=u.repo_id,
                        commit_sha=u.commit_sha,
                    )
                )

    def _resolve_or_external(
        self,
        *,
        qname: str,
        candidates: tuple[str, ...],
        resolver: QnameResolver,
        nodes: dict[str, GraphNode],
        kind_lookup: dict[str, NodeKind],
        repo_id: str,
        allowed_kinds: set[NodeKind],
    ) -> str:
        """Try each candidate qname in order; fall back to External."""
        for cand in candidates:
            if cand in resolver:
                unit_id, kind = resolver[cand]
                if kind in allowed_kinds:
                    # Make sure the kind lookup knows about cross-batch ids.
                    kind_lookup.setdefault(unit_id, kind)
                    return unit_id
        ext_id = _external_id(qname)
        if ext_id not in nodes:
            nodes[ext_id] = GraphNode(
                node_id=ext_id,
                kind=NodeKind.EXTERNAL,
                repo_id=repo_id,
                qualified_name=qname,
                name=qname.rsplit(".", 1)[-1],
            )
            kind_lookup[ext_id] = NodeKind.EXTERNAL
        return ext_id
