"""`explore` — directional graph neighborhood with content-bearing nodes.

Composition (read-only):
    1. resolve the seed qname/unit_id via the canonical Postgres store
    2. `graph_repo.neighbors(seed, depth)`   — undirected reachable set
    3. `graph_repo.edges_among(set + seed)`  — REAL directed edges
    4. directed BFS over those edges in the requested direction
    5. enrich every kept node from Postgres (signature, one-line snippet)

`_explore_impl` is the shared internal — the deprecated `query_graph`
and `get_related_components` aliases delegate here.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from core.mcp.execution.tool_executor import ExecutionContext
from core.mcp.schemas import ExploreRequest
from core.mcp.tools._helpers import (
    line_range,
    one_line_of,
    qname_suggestions,
    repo_ids,
    resolve_seed_unit,
    unknown_repo_payload,
)

# direction → (edge kind, orientation). "out" follows src→dst from the
# current node; "in" follows dst→src (i.e. who points AT the node).
_DIRECTION_RULES: dict[str, tuple[str, str]] = {
    "callers": ("CALLS", "in"),
    "callees": ("CALLS", "out"),
    "imports": ("IMPORTS", "out"),
    "imported_by": ("IMPORTS", "in"),
    "inherits": ("INHERITS", "out"),
}

_MAX_NEIGHBORS = 50


def _directed_bfs(
    seed_id: str,
    edges: list[tuple[str, str, str]],
    *,
    direction: str,
    depth: int,
) -> dict[str, tuple[int, str]]:
    """BFS from `seed_id` over directed edges → {node_id: (distance, relation)}.

    `relation` describes the FIRST edge that reached the node, phrased
    from the node's perspective (e.g. "calls seed" for a caller).
    """
    # adjacency: node -> [(other, edge_kind, orientation_from_node)]
    adj: dict[str, list[tuple[str, str, str]]] = {}
    for src, kind, dst in edges:
        adj.setdefault(src, []).append((dst, kind, "out"))
        adj.setdefault(dst, []).append((src, kind, "in"))

    rule = _DIRECTION_RULES.get(direction)
    found: dict[str, tuple[int, str]] = {}
    queue: deque[tuple[str, int]] = deque([(seed_id, 0)])
    seen = {seed_id}
    while queue:
        node, dist = queue.popleft()
        if dist >= depth:
            continue
        for other, kind, orient in sorted(adj.get(node, [])):
            if rule is not None and (kind, orient) != rule:
                continue
            if other in seen:
                continue
            seen.add(other)
            relation = f"{kind} {'->' if orient == 'out' else '<-'}"
            found[other] = (dist + 1, relation)
            queue.append((other, dist + 1))
    return found


async def _enrich_node(
    state: Any, node_id: str, graph_nodes: dict[str, Any]
) -> dict[str, Any]:
    """Self-contained neighbor entry: name, kind, file:line, signature, snippet.

    `node_id` rides along so the agent can feed it straight into
    read_unit (for non-External nodes it equals the unit_id).
    """
    gn = graph_nodes.get(node_id)
    if node_id.startswith("external:"):
        return {
            "node_id": node_id,
            "qualified_name": gn.qualified_name if gn else node_id[9:],
            "kind": "External",
            "file_path": None,
            "lines": None,
            "signature": None,
            "snippet": None,
        }
    unit = await state.units_repo.get_unit(node_id)
    if unit is not None:
        return {
            "node_id": node_id,
            "qualified_name": unit.qualified_name,
            "kind": unit.kind.value,
            "file_path": unit.file_path,
            "lines": line_range(unit),
            "signature": unit.signature,
            "snippet": one_line_of(unit.content),
        }
    return {
        "node_id": node_id,
        "qualified_name": gn.qualified_name if gn else node_id,
        "kind": gn.kind.value if gn else "unknown",
        "file_path": gn.file_path if gn else None,
        "lines": (
            f"{gn.line_start}-{gn.line_end}"
            if gn and gn.line_start is not None
            else None
        ),
        "signature": None,
        "snippet": None,
    }


async def _explore_impl(
    state: Any,
    *,
    reference: str,
    repo_id: str,
    direction: str,
    depth: int,
    request_id: str,
) -> dict[str, Any]:
    known = await repo_ids(state)
    if repo_id not in known:
        payload = await unknown_repo_payload(state, repo_id)
        payload["neighbors"] = []
        return payload

    seed = await resolve_seed_unit(state, repo_id=repo_id, reference=reference)
    if seed is None:
        suggestions = await qname_suggestions(state, repo_id, reference)
        return {
            "found": False,
            "qualified_name": reference,
            "neighbors": [],
            "suggestions": suggestions,
            "hint": (
                "Unknown symbol. Closest qualified_names are in "
                "`suggestions`; or use find_symbol(query=...) to browse."
            ),
        }

    graph_nodes_list = await state.graph_repo.neighbors(
        seed.unit_id, depth=depth
    )
    graph_nodes = {n.node_id: n for n in graph_nodes_list}
    all_ids = sorted({seed.unit_id, *graph_nodes})

    warning: str | None = None
    edges: list[tuple[str, str, str]] = []
    edges_among = getattr(state.graph_repo, "edges_among", None)
    if edges_among is not None:
        try:
            edges = [tuple(e) for e in await edges_among(all_ids)]
        except Exception as exc:
            warning = f"edge lookup failed ({type(exc).__name__}); " \
                      "direction filtering unavailable"
    else:
        warning = "graph backend lacks edges_among; direction filtering " \
                  "unavailable"

    if edges:
        reached = _directed_bfs(
            seed.unit_id, edges, direction=direction, depth=depth
        )
    elif direction == "all":
        # Degrade: undirected reachability only, relation unknown.
        reached = {
            nid: (1, "connected") for nid in graph_nodes if nid != seed.unit_id
        }
    else:
        reached = {}

    ordered_ids = sorted(reached, key=lambda nid: (reached[nid][0], nid))
    truncated = len(ordered_ids) > _MAX_NEIGHBORS
    neighbors: list[dict[str, Any]] = []
    for nid in ordered_ids[:_MAX_NEIGHBORS]:
        entry = await _enrich_node(state, nid, graph_nodes)
        entry["distance"], entry["relation"] = reached[nid]
        neighbors.append(entry)
    neighbors.sort(
        key=lambda n: (n["distance"], n["qualified_name"] or "")
    )

    out: dict[str, Any] = {
        "found": True,
        "seed": {
            "qualified_name": seed.qualified_name,
            "kind": seed.kind.value,
            "file_path": seed.file_path,
            "lines": line_range(seed),
            "signature": seed.signature,
        },
        "direction": direction,
        "depth": depth,
        "neighbors": neighbors,
        "edges": [
            {"src_id": s, "kind": k, "dst_id": d} for s, k, d in sorted(edges)
        ],
        "truncated": truncated,
    }
    if warning:
        out["warning"] = warning
    if not neighbors:
        out["hint"] = (
            f"No {direction} found within depth {depth}. Try "
            "direction='all', raise depth, or check the symbol with "
            "read_unit."
        )
    return out


class ExploreTool:
    """Directional graph traversal with self-contained neighbor entries."""

    name: str = "explore"
    description: str = (
        "Walk the code graph from one symbol: who calls it, what it "
        "calls, what it imports / is imported by, what it inherits — "
        "e.g. explore(qualified_name='core.retrieval.hybrid_retriever."
        "HybridRetriever.run', repo_id='memory-cl', direction='callers'). "
        "Every neighbor comes with kind, file:line, signature, and a "
        "one-line snippet, plus the real directed edges. Use AFTER you "
        "know a symbol exists (via search_code/find_symbol); use "
        "direction='all' with depth=1 to map a symbol's immediate "
        "context. Read-only."
    )
    request_schema = ExploreRequest

    async def execute(
        self, request: ExploreRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        return await _explore_impl(
            ctx.state,
            reference=request.qualified_name,
            repo_id=request.repo_id,
            direction=request.direction,
            depth=request.depth,
            request_id=ctx.request_id,
        )


__all__ = ["ExploreTool", "_explore_impl"]
