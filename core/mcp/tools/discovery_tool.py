"""Discovery tools — `find_symbol`, `list_repos`, `repo_overview`.

These are the orientation surface for an agent that just connected:
what's ingested, what a repo looks like, and where a symbol lives.
All three are read-only composition over the canonical Postgres store
plus (for repo_overview) the Neo4j repo graph.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from core.mcp.execution.tool_executor import ExecutionContext
from core.mcp.schemas import (
    FindSymbolRequest,
    ListReposRequest,
    RepoOverviewRequest,
)
from core.mcp.tools._helpers import (
    escape_like,
    repo_ids,
    unknown_repo_payload,
)

_DOC_SUFFIXES = (".md", ".rst", ".txt")


# ---------------------------------------------------------------------------
# find_symbol
# ---------------------------------------------------------------------------
async def _find_in_repo(
    state: Any, repo_id: str, query: str, limit: int
) -> list[dict[str, Any]]:
    """Substring qname search, enriched with kind + file:line + unit_id.

    Fetches `limit + 1` rows so the caller can detect truncation: if
    the extra row arrives, the result was capped and `truncated` can be
    set honestly. The extra row is never included in the returned slice.
    Read-only.
    """
    from sqlalchemy import text

    # ILIKE on Postgres, LIKE (ASCII case-insensitive) on lite/SQLite.
    _d = getattr(getattr(state.postgres.engine, "dialect", None), "name", None)
    like = "ILIKE" if (_d or "postgresql") == "postgresql" else "LIKE"
    sql = text(
        "SELECT unit_id, qualified_name, kind, file_path, line_start, line_end"
        "  FROM ingestion_units"
        f" WHERE repo_id = :repo_id AND qualified_name {like} :pattern"
        " ORDER BY length(qualified_name), qualified_name"
        " LIMIT :limit"
    )
    async with state.postgres.engine.connect() as conn:
        result = await conn.execute(
            sql,
            {
                "repo_id": repo_id,
                "pattern": f"%{escape_like(query)}%",
                "limit": limit + 1,  # fetch one extra to detect truncation
            },
        )
        rows = result.all()
    out: list[dict[str, Any]] = []
    for row in rows:
        m = row._mapping if hasattr(row, "_mapping") else row
        out.append(
            {
                "repo_id": repo_id,
                "qualified_name": m["qualified_name"],
                "kind": m["kind"],
                "file_path": m["file_path"],
                "lines": f"{m['line_start']}-{m['line_end']}",
                "unit_id": m["unit_id"],
            }
        )
    return out


class FindSymbolTool:
    """Substring/fuzzy qualified-name lookup."""

    name: str = "find_symbol"
    description: str = (
        "Find symbols by (partial) name: case-insensitive substring "
        "match over qualified names, e.g. find_symbol(query='HybridRetr', "
        "repo_id='memory-cl'). Returns qualified_name, kind, and "
        "file:line for each match — feed the qualified_name into "
        "read_unit or explore next. Omit repo_id to search every repo. "
        "Use this when you know (part of) a NAME; use search_code for "
        "natural-language questions. Read-only."
    )
    request_schema = FindSymbolRequest

    async def execute(
        self, request: FindSymbolRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        state = ctx.state
        known = await repo_ids(state)
        if request.repo_id is not None:
            if request.repo_id not in known:
                payload = await unknown_repo_payload(state, request.repo_id)
                payload["matches"] = []
                return payload
            targets = [request.repo_id]
        else:
            targets = known

        matches: list[dict[str, Any]] = []
        truncated = False
        for repo in targets:
            rows = await _find_in_repo(state, repo, request.query, request.limit)
            if len(rows) > request.limit:
                truncated = True
                rows = rows[: request.limit]
            matches.extend(rows)
        # Deterministic: shortest qname first (canonical units beat
        # deeply nested test paths), then qname, then repo.
        matches.sort(
            key=lambda m: (
                len(m["qualified_name"]),
                m["qualified_name"],
                m["repo_id"],
            )
        )
        # After merging across repos, apply the global limit.
        if len(matches) > request.limit:
            truncated = True
            matches = matches[: request.limit]

        out: dict[str, Any] = {"matches": matches, "truncated": truncated}
        if not matches:
            out["hint"] = (
                "No qualified name contains that substring. Try a shorter "
                "fragment, search_code for a semantic search, or "
                "repo_overview to see the module tree."
            )
        return out


# ---------------------------------------------------------------------------
# list_repos
# ---------------------------------------------------------------------------
class ListReposTool:
    """Every ingested repo with unit/file counts and languages."""

    name: str = "list_repos"
    description: str = (
        "List every ingested repository with unit/file counts and "
        "languages. Call this FIRST when you connect — every other tool "
        "needs a repo_id from here. Takes no arguments: list_repos(). "
        "Then use repo_overview(repo_id=...) to see a repo's structure. "
        "Read-only."
    )
    request_schema = ListReposRequest

    async def execute(
        self, request: ListReposRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        summaries = await ctx.state.units_repo.list_repos()
        repos = sorted(
            (
                {
                    "repo_id": s.repo_id,
                    "units": s.units,
                    "files": s.files,
                    "languages": sorted(s.languages),
                }
                for s in summaries
            ),
            key=lambda r: r["repo_id"],
        )
        return {
            "repos": repos,
            "hint": (
                "Use repo_overview(repo_id=...) for a repo's structure, "
                "then search_code / find_symbol to dig in."
                if repos
                else "Nothing ingested yet — use ingest_repository(path=..., "
                "repo_id=...) to add a repo."
            ),
        }


# ---------------------------------------------------------------------------
# repo_overview
# ---------------------------------------------------------------------------
async def _fetch_overview_rows(state: Any, repo_id: str) -> list[dict[str, Any]]:
    """One light scan: qname/kind/file/language/lines for every unit."""
    from sqlalchemy import text

    sql = text(
        "SELECT qualified_name, kind, file_path, language, line_start, line_end"
        "  FROM ingestion_units WHERE repo_id = :repo_id"
        " ORDER BY qualified_name"
    )
    async with state.postgres.engine.connect() as conn:
        result = await conn.execute(sql, {"repo_id": repo_id})
        rows = result.all()
    return [
        dict(r._mapping if hasattr(r, "_mapping") else r) for r in rows
    ]


def _module_tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Top-level qname segments with descendant counts + child modules."""
    top: dict[str, dict[str, Any]] = {}
    module_qnames = sorted(
        r["qualified_name"] for r in rows if r["kind"] == "mod"
    )
    for row in rows:
        head = row["qualified_name"].split(".", 1)[0]
        entry = top.setdefault(head, {"name": head, "units": 0, "modules": set()})
        entry["units"] += 1
    for qname in module_qnames:
        parts = qname.split(".")
        if parts[0] in top and len(parts) <= 2:
            top[parts[0]]["modules"].add(qname)
    return [
        {
            "name": e["name"],
            "units": e["units"],
            "modules": sorted(e["modules"]),
        }
        for e in sorted(top.values(), key=lambda e: (-e["units"], e["name"]))
    ]


def _largest_modules(
    rows: list[dict[str, Any]], limit: int = 10
) -> list[dict[str, Any]]:
    """Modules ranked by descendant unit count (qname-prefix containment)."""
    modules = [
        (r["qualified_name"], r["file_path"])
        for r in rows
        if r["kind"] == "mod"
    ]
    counts: Counter[str] = Counter()
    qnames = [r["qualified_name"] for r in rows]
    for mod_qname, _ in modules:
        prefix = mod_qname + "."
        counts[mod_qname] = sum(
            1 for q in qnames if q == mod_qname or q.startswith(prefix)
        )
    by_file = dict(modules)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [
        {"qualified_name": q, "units": n, "file_path": by_file.get(q)}
        for q, n in ranked
    ]


async def _most_connected(
    state: Any, repo_id: str, limit: int = 10
) -> list[dict[str, Any]] | None:
    """Highest-degree graph nodes, from the whole-repo graph snapshot."""
    try:
        nodes, edges = await state.graph_repo.repo_graph(
            repo_id, include_external=False, max_nodes=5000
        )
    except Exception:
        return None
    degree: Counter[str] = Counter()
    for src, _kind, dst in edges:
        degree[src] += 1
        degree[dst] += 1
    by_id = {n.node_id: n for n in nodes}
    ranked = sorted(
        degree.items(), key=lambda kv: (-kv[1], kv[0])
    )
    out: list[dict[str, Any]] = []
    for node_id, deg in ranked:
        n = by_id.get(node_id)
        if n is None:
            continue
        out.append(
            {
                "qualified_name": n.qualified_name,
                "kind": n.kind.value,
                "file_path": n.file_path,
                "connections": deg,
            }
        )
        if len(out) >= limit:
            break
    return out


class RepoOverviewTool:
    """Structural orientation for one repo."""

    name: str = "repo_overview"
    description: str = (
        "Get oriented in one repo: language and unit-kind breakdowns, "
        "the top-level module tree, the largest and most-connected "
        "modules, and any doc files — e.g. "
        "repo_overview(repo_id='memory-cl'). Call this right after "
        "list_repos, before searching, so your search_code/find_symbol "
        "calls target the right modules. Read-only."
    )
    request_schema = RepoOverviewRequest

    async def execute(
        self, request: RepoOverviewRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        state = ctx.state
        rows = await _fetch_overview_rows(state, request.repo_id)
        if not rows:
            return await unknown_repo_payload(state, request.repo_id)

        languages = Counter(r["language"] for r in rows)
        kinds = Counter(r["kind"] for r in rows)
        files = {r["file_path"] for r in rows}
        doc_files = sorted(
            f for f in files if f and f.lower().endswith(_DOC_SUFFIXES)
        )

        out: dict[str, Any] = {
            "found": True,
            "repo_id": request.repo_id,
            "units": len(rows),
            "files": len(files),
            "languages": dict(sorted(languages.items())),
            "unit_kinds": dict(sorted(kinds.items())),
            "module_tree": _module_tree(rows),
            "largest_modules": _largest_modules(rows),
            "doc_files": doc_files,
            "hint": (
                "Drill in with find_symbol(query=<module name>), "
                "read_file(file_path=...), or search_code(question=...)."
            ),
        }
        connected = await _most_connected(state, request.repo_id)
        if connected is not None:
            out["most_connected"] = connected
        else:
            out["note"] = "graph backend unavailable — most_connected omitted"
        return out


__all__ = ["FindSymbolTool", "ListReposTool", "RepoOverviewTool"]
