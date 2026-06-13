# MCP Tools v2 — agent-first tool surface

**Date:** 2026-06-12 · **Branch:** `feat/mcp-tools-v2`

## Problem

The 7 existing tools are internal-phase relics: they return `unit_id`s without
content, descriptions explain our architecture instead of when-to-use-me, and an
agent connecting cold cannot orient itself (no repo listing, no file reading, no
symbol search). The MCP surface exists for agents — every response must be
self-contained, every description must teach.

## Design principles

1. **Content, not ids.** Every hit carries qualified_name, kind, file:line, and a
   real code snippet. Agents never need a second lookup to see what a hit *is*.
2. **Descriptions written to the agent.** When to use, when NOT to, one inline
   example call. Every request field carries a description.
3. **Token-aware.** Responses are capped (~8 000 estimated tokens, `ceil(len/4)`)
   with explicit `truncated` flags at both response and per-snippet level.
4. **Teaching errors.** Unknown repo → list valid repo_ids. Unknown qname → 5
   closest matches via `search_qnames`. Empty results → suggest the next tool.
   These are returned as `status=success` with `found=false` + `hint`/`suggestions`
   fields (matching the existing tool idiom) so agents always see the guidance.
5. **Deterministic ordering** everywhere (score desc → qualified_name asc, or
   line order, or distance asc).
6. **Read-only composition.** New tools compose existing Phase 2–4 APIs
   (`HybridRetriever`, `RankingModel`, `units_repo`, `graph_repo.neighbors` /
   `edges_among` / `repo_graph`, plus read-only SQL over `ingestion_units`).
   No storage-layer changes.

## Tool list (14)

| Tool | Purpose | Key return shape |
|---|---|---|
| `search_code(question, repo_id?, top_k=8)` | Hybrid (vector+graph+metadata) semantic search. `repo_id` omitted → fan-in across ALL repos, hits attributed per repo. | `results: [{repo_id, qualified_name, kind, file_path, lines, score, channels, snippet, snippet_truncated}]`, `truncated`, `hint?` |
| `read_unit(reference, repo_id?)` | Full unit by qualified_name OR unit_id OR file_path. Fuzzy assist on miss. | `unit: {qualified_name, kind, file_path, lines, signature, docstring, imports, calls, bases, parent_chain, content}`, `truncated` |
| `explore(qualified_name, repo_id, direction="all", depth=1)` | Graph neighborhood. Directions: callers/callees/imports/imported_by/inherits/all. Directed BFS over `neighbors()` + `edges_among()`. | `seed`, `neighbors: [{node_id, qualified_name, kind, file_path, lines, signature, snippet, relation, distance}]`, `edges` |
| `find_symbol(query, repo_id?, limit=20)` | Substring qname search with kind + file:line (the /qnames capability, enriched). | `matches: [{qualified_name, kind, file_path, lines, unit_id}]` |
| `list_repos()` | What's ingested. First call for a cold agent. | `repos: [{repo_id, units, files, languages}]`, `hint` |
| `repo_overview(repo_id)` | Orientation: language/kind breakdown, top-level tree, largest + most-connected modules, doc files. | aggregates from one light SQL scan + `repo_graph` degrees |
| `read_file(file_path, repo_id)` | Whole file stitched from its units in line order (module units already carry full file source). | `content`, `units` outline, `truncated` |
| `get_module_summary(module, repo_id)` | KEPT — dense structural summary of one module. | unchanged |
| `get_risks(entity, repo_id)` | KEPT — structural external-dependency risks. | unchanged |
| `update_memory(...)` | KEPT — description now warns it MUTATES session state. | unchanged |
| `ingest_repository(...)` | KEPT — description now warns it MUTATES all three stores and is slow. | unchanged |
| `query_graph` | DEPRECATED alias → delegates to `explore` internals (direction="all"). | v2 explore shape |
| `get_related_components` | DEPRECATED alias → delegates to `explore` internals, seed stripped. | v2 explore shape |
| `get_context` | DEPRECATED alias → delegates to `search_code` internals. | v2 search shape |

## Decisions

- **Deprecated aliases return v2 shapes** plus a thin v1-compat layer: the
  alias guarantee is "the tool name still resolves and does the right thing".
  `query_graph` re-adds `candidates[].unit_id` and `get_related_components`
  re-adds `related` (derived from v2 neighbors) because the in-repo SDK's
  `query_graph` wrapper reads them. Descriptions start "DEPRECATED — use X",
  and every alias response carries `deprecated: "use X"`.
- **`Tool` Protocol loosened** (`execute(request: Any)`, `request_schema` as a
  read-only property) so concrete tools can declare narrowed request types
  without tripping mypy's invariance — this also cleared 7 pre-existing
  registry type errors.
- **Direction filtering**: `neighbors()` is undirected reachability; real
  direction comes from `edges_among({seed} ∪ neighborhood)` (directed), then a
  directed BFS from the seed following only the requested edge kind/orientation.
  If the graph backend lacks `edges_among`, direction-filtered calls degrade to
  the undirected list with a `warning`.
- **`find_symbol` uses tool-level SQL** (same ILIKE/escaping semantics as
  `search_qnames`) because the repository method doesn't return file/line and
  v2 must be self-contained without touching the storage layer.
- **`description` becomes a first-class Tool attribute**, surfaced identically by
  both transports: the HTTP `GET /mcp/tools` listing gains a `description` field,
  and the native MCP server's `_to_protocol_tool` prefers `tool.description` over
  the class docstring. Both transports already share one `ToolRegistry`
  (wired in `apps/api/lifespan.py`) — verified, no divergence.
- **Multi-repo fan-in** iterates `list_repos()` in sorted repo order; per-repo
  retrieval failures are recorded in `failed_repos` instead of failing the call.

## Out of scope

Storage-layer changes, new endpoints, semantic risk analysis, write tools.
