"""`search_code` — agent-facing hybrid retrieval with real content.

This is the v2 front door: vector + graph + metadata retrieval fused
and ranked (the same Phase-4 path `get_context` used), but every hit
ships a code snippet so the agent never needs a follow-up lookup.

`_search_impl` is the shared internal — the deprecated `get_context`
alias delegates here.
"""

from __future__ import annotations

from typing import Any

from core import get_settings
from core.mcp.execution.tool_executor import ExecutionContext
from core.mcp.schemas import SearchCodeRequest
from core.mcp.tools._helpers import (
    MAX_RESPONSE_TOKENS,
    build_hybrid_retriever,
    build_ranking_model,
    estimate_tokens,
    repo_ids,
    snippet_of,
    unknown_repo_payload,
)
from schemas import Query

_EMPTY_HINT = (
    "No results. Try find_symbol(query=...) for exact symbol names, "
    "repo_overview(repo_id=...) to see what the repo contains, or "
    "rephrase the question in plainer prose."
)


async def _search_one_repo(
    state: Any, *, question: str, repo_id: str, top_k: int, request_id: str
) -> list[dict[str, Any]]:
    """Hybrid retrieve + rank one repo; hits carry content snippets."""
    settings = get_settings()
    hybrid = build_hybrid_retriever(
        state, repo_id=repo_id, max_depth=settings.max_graph_traversal_depth
    )
    result = await hybrid.run(
        Query(text=question, repo_id=repo_id, top_k=top_k),
        query_id=request_id,
    )
    ranked = build_ranking_model().rank(
        result.candidates, top_k=top_k, query_id=request_id, repo_id=repo_id
    )

    hits: list[dict[str, Any]] = []
    for r in ranked:
        unit = await state.units_repo.get_unit(r.unit_id)
        snippet, clipped = snippet_of(unit.content if unit else None)
        hits.append(
            {
                "repo_id": repo_id,
                "qualified_name": r.qualified_name
                or (unit.qualified_name if unit else r.unit_id),
                "kind": r.kind or (unit.kind.value if unit else None),
                "file_path": r.file_path or (unit.file_path if unit else None),
                "lines": f"{unit.line_start}-{unit.line_end}" if unit else None,
                "score": round(r.final_score, 4),
                "channels": [c.value for c in r.channels],
                "snippet": snippet,
                "snippet_truncated": clipped,
            }
        )
    return hits


async def _search_impl(
    state: Any, *, question: str, repo_id: str | None, top_k: int, request_id: str
) -> dict[str, Any]:
    """Shared search core for `search_code` and the `get_context` alias."""
    known = await repo_ids(state)
    if repo_id is not None:
        if repo_id not in known:
            payload = await unknown_repo_payload(state, repo_id)
            payload["results"] = []
            return payload
        targets = [repo_id]
    else:
        if not known:
            return {
                "results": [],
                "truncated": False,
                "hint": (
                    "No repositories are ingested yet — use "
                    "ingest_repository to add one, then retry."
                ),
            }
        targets = known  # already sorted — deterministic fan-in order

    all_hits: list[dict[str, Any]] = []
    failed_repos: list[str] = []
    for target in targets:
        try:
            all_hits.extend(
                await _search_one_repo(
                    state,
                    question=question,
                    repo_id=target,
                    top_k=top_k,
                    request_id=request_id,
                )
            )
        except Exception:
            # Per-repo isolation: one broken collection must not sink a
            # fan-in search across every other repo.
            if len(targets) == 1:
                raise
            failed_repos.append(target)

    # Deterministic merge: score desc, then qname/repo for stable ties.
    all_hits.sort(
        key=lambda h: (
            -h["score"],
            h["qualified_name"] or "",
            h["repo_id"],
        )
    )

    # Token budget: keep whole hits until the cap; flag the cut.
    results: list[dict[str, Any]] = []
    spent = 0
    truncated = False
    for hit in all_hits:
        cost = estimate_tokens(hit["snippet"]) + 50  # ~metadata overhead
        if results and spent + cost > MAX_RESPONSE_TOKENS:
            truncated = True
            break
        spent += cost
        results.append(hit)

    out: dict[str, Any] = {
        "results": results,
        "total_matches": len(all_hits),
        "truncated": truncated,
    }
    if truncated:
        out["hint"] = (
            "Response hit the token cap — results were cut after "
            f"{len(results)} of {len(all_hits)} hits. Lower top_k or "
            "pass repo_id to narrow the search."
        )
    if failed_repos:
        out["failed_repos"] = sorted(failed_repos)
    if not all_hits:
        out["hint"] = _EMPTY_HINT
    return out


class SearchCodeTool:
    """Semantic + structural code search returning content-bearing hits."""

    name: str = "search_code"
    description: str = (
        "Search ingested code with a natural-language question (hybrid "
        "vector + graph + keyword retrieval). Use this FIRST when you "
        "need to find where something happens, e.g. "
        "search_code(question='where are JA4 fingerprints parsed?', "
        "repo_id='ja4m'). Omit repo_id to search every repo at once. "
        "Each hit includes file:line and a code snippet — no follow-up "
        "call needed to see the code. NOT for exact symbol-name lookup "
        "(use find_symbol) or reading a whole file (use read_file). "
        "Read-only."
    )
    request_schema = SearchCodeRequest

    async def execute(
        self, request: SearchCodeRequest, ctx: ExecutionContext
    ) -> dict[str, Any]:
        return await _search_impl(
            ctx.state,
            question=request.question,
            repo_id=request.repo_id,
            top_k=request.top_k,
            request_id=ctx.request_id,
        )


__all__ = ["SearchCodeTool", "_search_impl"]
