# 08 · MCP Tooling

← back to [index](00_INDEX.md) · related: [07_API_REFERENCE](07_API_REFERENCE.md), [22_SECURITY_AND_ACCESS_CONTROL](22_SECURITY_AND_ACCESS_CONTROL.md), [03_DATA_FLOW](03_DATA_FLOW.md)

The MCP layer (Phase 5) is the **agent surface**. It exposes Phases
2–4 as a small, typed, deterministic tool set. Tools are pure
orchestration wrappers — they never bypass retrieval / graph /
ingestion semantics.

## The tool registry (14 tools)

The v2 surface is **agent-first**: discovery/read/explore tools front the
hybrid engine, with the original Phase-4 tools kept (some as compat aliases).

| Tool | Wraps | Purpose |
|---|---|---|
| `search_code` | hybrid retrieval | Natural-language code search (the primary entry point) |
| `read_unit` | Postgres/SQLite read | Read one unit by qualified name (with parent chain) |
| `read_file` | Postgres/SQLite read | Read a file's units (fuzzy path match) |
| `explore` | `GraphRetriever` (BFS) | Walk a symbol's neighbors with relation labels |
| `find_symbol` | qname search | Locate symbols by name fragment |
| `list_repos` | `units_repo.list_repos` | Ingested repos + unit/file/language counts |
| `repo_overview` | aggregates | Repo shape: top modules, languages, most-connected |
| `get_context` | full Phase-4 retrieval | Returns a `ContextPacket` for a task |
| `get_module_summary` | read + `ModuleSummarizer` | Per-module `DenseModule` |
| `get_related_components` | `GraphRetriever` (BFS) | 1+-hop neighbors of a unit/qname |
| `get_risks` | `neighbors` filtered to `EXTERNAL` | Foreign-dependency risk projection |
| `query_graph` | `GraphRetriever` (depth-bounded) | BFS exposing seed + neighbors |
| `ingest_repository` | `IngestionPipeline` | Trigger ingest from agent context |
| `update_memory` | Redis/in-memory `RPUSH + EXPIRE` | Append-only session memory |

Source: `core/mcp/tools/` · registry: `apps/mcp/registry.py::build_default_registry()`
(14 tools — the live count is asserted by `tests/test_mcp_tools.py::EXPECTED_TOOLS`).

## Registry model

`core/mcp/execution/tool_executor.py::ToolRegistry`:

- name → tool instance map (sorted iteration)
- name uniqueness enforced
- `Tool` is a `@runtime_checkable` Protocol — duck-typed tools work

`apps/mcp/registry.py::build_default_registry()` wires the 14
defaults at boot. Tests can construct ad-hoc registries with fakes.

## Execution lifecycle

```
HTTP request
  ↓
apps/mcp/router.py
  ↓ ExecutionContext.new(state, user_scope, request_id)
core/mcp/execution/tool_executor.py · ToolExecutor.execute()
  │
  │  1. registry.get(name)            → unknown_tool if missing
  │  2. validate_tool_request(payload, schema)  → validation_error on bad shape
  │  3. tool.execute(request, ctx)    → backend_error on raise
  │  4. wrap result in ToolResponse
  │  5. emit mcp_tool_call audit event
  ↓
ToolResponse (always HTTP 200)
```

## Validation rules

- Every request schema lives in `core/mcp/schemas/tool_request.py`.
- Every schema uses `extra="forbid"`. Unknown fields → `validation_error`.
- Field-level constraints (min/max length, value bounds) are enforced
  by Pydantic and surfaced verbatim in `ToolResponse.data.errors[]`.

`validate_tool_request()` re-shapes Pydantic errors into:
```json
{"loc": ["field"], "msg": "...", "type": "..."}
```
so clients have a stable error format.

## Error model

| `error_code` | When | HTTP |
|---|---|---|
| `validation_error` | Pydantic rejected the body | 200 |
| `unauthorized` | Reserved for tool-level perm checks (Phase 11+) | 200 |
| `unknown_tool` | Name not in registry | 200 |
| `backend_error` | Tool implementation raised | 200 |
| `internal_error` | Reserved | 200 |

The executor **never raises** to the server. This is the spec:
"do NOT crash MCP server" → tested by
`test_executor_never_raises`.

## Audit trail

Every tool call (success OR failure) emits a hash-chained `audit_event`
with action set to the tool name. See [16_AUDIT_AND_GOVERNANCE](16_AUDIT_AND_GOVERNANCE.md).

## Determinism

- `get_context` round-trip is byte-deterministic for same query +
  state — pinned by `test_get_context_is_deterministic_across_calls`.
- `query_graph` BFS is deterministic given sorted seeds + sorted
  neighbors per visit.
- `update_memory` writes canonical-JSON encoded entries (sorted keys).

## Adding a tool

See [05_LOCAL_DEVELOPMENT](05_LOCAL_DEVELOPMENT.md) → "Add a new MCP tool".

## Auth

- Dev mode: `MCP_API_KEY` unset → no auth.
- Production: set `MCP_API_KEY`, pass via `X-API-Key` or `Authorization: Bearer`.
- Wrong key / missing key → HTTP 401 (NOT in-band — auth is the only
  out-of-band failure).

See [22_SECURITY_AND_ACCESS_CONTROL](22_SECURITY_AND_ACCESS_CONTROL.md).

---

Next: [09 — Retrieval System](09_RETRIEVAL_SYSTEM.md)
