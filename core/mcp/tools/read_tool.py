"""`read_unit` and `read_file` — full-content reads from the canonical store.

Both compose existing Phase-2 read APIs (`units_repo.get_unit`,
`units_repo.list_units_for_file`, read-only SQL by qualified_name) and
return token-capped content with explicit `truncated` flags. Misses are
teaching errors: closest qnames / file paths plus the next tool to try.
"""

from __future__ import annotations

from typing import Any

from core.mcp.execution.tool_executor import ExecutionContext
from core.mcp.schemas import ReadFileRequest, ReadUnitRequest
from core.mcp.tools._helpers import (
    MAX_RESPONSE_TOKENS,
    fetch_unit_by_qname,
    line_range,
    looks_like_unit_id,
    qname_suggestions,
    repo_ids,
    truncate_to_budget,
    unknown_repo_payload,
)
from schemas import IngestionUnit


def _looks_like_path(reference: str) -> bool:
    return "/" in reference or "\\" in reference


async def _parent_chain(
    state: Any, unit: IngestionUnit, max_hops: int = 5
) -> list[dict[str, str]]:
    """Enclosing scopes, innermost first: method → class → module."""
    chain: list[dict[str, str]] = []
    parent_qname = unit.parent_qualified_name
    for _ in range(max_hops):
        if not parent_qname:
            break
        parent = await fetch_unit_by_qname(
            state.postgres.engine, unit.repo_id, parent_qname
        )
        if parent is None:
            chain.append({"qualified_name": parent_qname, "kind": "unknown"})
            break
        chain.append(
            {"qualified_name": parent.qualified_name, "kind": parent.kind.value}
        )
        parent_qname = parent.parent_qualified_name
    return chain


def _unit_payload(unit: IngestionUnit) -> dict[str, Any]:
    content, truncated = truncate_to_budget(unit.content, MAX_RESPONSE_TOKENS)
    return {
        "unit_id": unit.unit_id,
        "repo_id": unit.repo_id,
        "qualified_name": unit.qualified_name,
        "kind": unit.kind.value,
        "file_path": unit.file_path,
        "lines": line_range(unit),
        "language": unit.language.value,
        "signature": unit.signature,
        "docstring": unit.docstring,
        "imports": list(unit.imports),
        "calls": list(unit.calls),
        "bases": list(unit.bases),
        "content": content,
        "truncated": truncated,
    }


async def _resolve_reference(
    state: Any, *, repo_id: str, reference: str
) -> IngestionUnit | None:
    """unit_id → exact qname → file path (module unit), in that order."""
    unit: IngestionUnit | None
    if looks_like_unit_id(reference):
        unit = await state.units_repo.get_unit(reference)
        if unit is not None:
            return unit
    unit = await fetch_unit_by_qname(state.postgres.engine, repo_id, reference)
    if unit is not None:
        return unit
    if _looks_like_path(reference):
        units: list[IngestionUnit] = list(
            await state.units_repo.list_units_for_file(repo_id, reference)
        )
        if units:
            # Prefer the module unit (full file source); else widest span.
            units.sort(key=lambda u: (u.line_start, -(u.line_end)))
            for u in units:
                if u.kind.value == "mod":
                    return u
            return units[0]
    return None


class ReadUnitTool:
    """Read one code unit in full, with structure metadata + parent chain."""

    name: str = "read_unit"
    description: str = (
        "Read one code unit (function, class, method, module) in full: "
        "content, signature, docstring, imports, calls, base classes, "
        "and the enclosing parent chain. `reference` accepts a "
        "qualified_name, a 64-char unit_id, or a file path — e.g. "
        "read_unit(reference='core.retrieval.hybrid_retriever."
        "HybridRetriever', repo_id='memory-cl'). Use after search_code/"
        "find_symbol told you WHAT exists; use read_file instead when "
        "you want a whole file. Read-only."
    )
    request_schema = ReadUnitRequest

    async def execute(
        self, request: ReadUnitRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        state = ctx.state
        known = await repo_ids(state)

        if request.repo_id is not None:
            if request.repo_id not in known:
                return await unknown_repo_payload(state, request.repo_id)
            targets = [request.repo_id]
        else:
            targets = known

        for repo in targets:
            unit = await _resolve_reference(
                state, repo_id=repo, reference=request.reference
            )
            if unit is not None:
                payload = _unit_payload(unit)
                payload["parent_chain"] = await _parent_chain(state, unit)
                payload["found"] = True
                return payload

        # Teaching error: closest qnames across the searched repos.
        suggestions: list[dict[str, str]] = []
        for repo in targets:
            for s in await qname_suggestions(state, repo, request.reference):
                suggestions.append({**s, "repo_id": repo})
            if len(suggestions) >= 5:
                break
        return {
            "found": False,
            "reference": request.reference,
            "suggestions": suggestions[:5],
            "hint": (
                "No unit matched. Closest qualified_names are in "
                "`suggestions` — retry with one of them, or use "
                "find_symbol(query=...) to browse matches."
                if suggestions
                else "No unit matched and nothing similar was found. Use "
                "find_symbol(query=...) or list_repos() to orient."
            ),
        }


class ReadFileTool:
    """Read a whole source file, stitched from its ingested units."""

    name: str = "read_file"
    description: str = (
        "Read an entire source file from an ingested repo, e.g. "
        "read_file(file_path='core/retrieval/hybrid_retriever.py', "
        "repo_id='memory-cl'). Returns the file content plus an outline "
        "of the units (classes/functions) it defines with line ranges. "
        "Use when you need full-file context; for a single symbol "
        "read_unit is cheaper. Large files are token-capped with "
        "truncated=true. Read-only."
    )
    request_schema = ReadFileRequest

    async def execute(
        self, request: ReadFileRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        state = ctx.state
        known = await repo_ids(state)
        if request.repo_id not in known:
            return await unknown_repo_payload(state, request.repo_id)

        units = list(
            await state.units_repo.list_units_for_file(
                request.repo_id, request.file_path
            )
        )
        if not units:
            similar = await _similar_paths(
                state, request.repo_id, request.file_path
            )
            return {
                "found": False,
                "file_path": request.file_path,
                "similar_paths": similar,
                "hint": (
                    "No units for that path. Paths are repo-relative and "
                    "case-sensitive — `similar_paths` lists close matches."
                    if similar
                    else "No units for that path. Use repo_overview or "
                    "find_symbol to discover valid file paths."
                ),
            }

        content = _stitch(units)
        content, truncated = truncate_to_budget(content, MAX_RESPONSE_TOKENS)
        outline = [
            {
                "qualified_name": u.qualified_name,
                "kind": u.kind.value,
                "lines": line_range(u),
            }
            for u in sorted(
                units, key=lambda u: (u.line_start, u.qualified_name)
            )
        ]
        out: dict[str, Any] = {
            "found": True,
            "repo_id": request.repo_id,
            "file_path": request.file_path,
            "language": units[0].language.value,
            "content": content,
            "units": outline,
            "truncated": truncated,
        }
        if truncated:
            out["hint"] = (
                "File content hit the token cap. Use read_unit on a "
                "specific unit from `units` to read the rest."
            )
        return out


def _stitch(units: list[IngestionUnit]) -> str:
    """Reassemble file content from its units in line order.

    Parsers emit a module unit whose content IS the full file source —
    when present (it spans from line 1 outward) we use it directly.
    Otherwise we concatenate top-level spans, skipping units fully
    contained in an already-covered range (methods inside classes).
    """
    ordered = sorted(units, key=lambda u: (u.line_start, -(u.line_end)))
    for u in ordered:
        if u.kind.value == "mod" and u.line_start == 1:
            return u.content
    parts: list[str] = []
    covered_end = 0
    for u in ordered:
        if u.line_end <= covered_end:
            continue  # nested in a span we already included
        parts.append(u.content.rstrip("\n"))
        covered_end = max(covered_end, u.line_end)
    return "\n\n".join(parts)


async def _similar_paths(
    state: Any, repo_id: str, file_path: str, limit: int = 5
) -> list[str]:
    """Closest stored file paths, matched on the basename."""
    from sqlalchemy import text

    from core.mcp.tools._helpers import escape_like

    basename = file_path.replace("\\", "/").rsplit("/", 1)[-1]
    if not basename:
        return []
    sql = text(
        "SELECT DISTINCT file_path FROM ingestion_units "
        " WHERE repo_id = :repo_id AND file_path ILIKE :pattern "
        " ORDER BY file_path LIMIT :limit"
    )
    try:
        async with state.postgres.engine.connect() as conn:
            result = await conn.execute(
                sql,
                {
                    "repo_id": repo_id,
                    "pattern": f"%{escape_like(basename)}%",
                    "limit": limit,
                },
            )
            rows = result.all()
    except Exception:
        return []
    return [
        (r._mapping if hasattr(r, "_mapping") else r)["file_path"] for r in rows
    ]


__all__ = ["ReadFileTool", "ReadUnitTool"]
