# 05 · Local Development

← back to [index](00_INDEX.md) · related: [04_INSTALLATION](04_INSTALLATION.md), [06_CONFIGURATION](06_CONFIGURATION.md), [24_TROUBLESHOOTING](24_TROUBLESHOOTING.md)

## Daily loop

```bash
# 1. backend services
docker compose up -d postgres neo4j qdrant redis

# 2. backend with hot reload
.venv/bin/uvicorn apps.api.main:app --reload --port 8000

# 3. UI hot reload (separate terminal)
cd ui && npm run dev
```

The `--reload` flag watches `apps/`, `core/`, `storage/`, `schemas/`,
and `infra/`. Any change triggers a restart.

## Tests

```bash
# everything (442 across phases 1–9)
.venv/bin/pytest -q

# one file
.venv/bin/pytest tests/test_python_parser.py -v

# one test
.venv/bin/pytest tests/test_phase8_audit.py::test_chain_links_consecutive_entries -v

# one phase's golden gate
.venv/bin/pytest tests/test_golden_phase4.py -v

# with coverage
.venv/bin/pytest --cov=core --cov=apps --cov=storage -q
```

Tests are designed to be self-contained — they use mocks / fakes for
the four storage backends so you don't need Docker running for the
unit suite. The golden tests use the in-tree fixture
(`tests/fixtures/sample_repo/`).

## Lint + type check

```bash
.venv/bin/ruff check .       # zero errors expected on main
.venv/bin/ruff check . --fix # auto-fix safe issues
.venv/bin/mypy core storage apps  # optional; CI doesn't gate on this yet
```

## Common dev tasks

### Add a new MCP tool

1. Define request schema in `core/mcp/schemas/tool_request.py`.
2. Implement the tool in `core/mcp/tools/<name>_tool.py` — must
   conform to the `Tool` protocol (`name`, `request_schema`,
   `async execute(request, ctx)`).
3. Register it in `apps/mcp/registry.py::build_default_registry()`.
4. Test it: `tests/test_mcp_tools.py` patterns.
5. Bump `MCP_TOOL_COUNT` expectations (only if tests assert exact
   counts — `test_default_registry_exposes_v2_surface` is the
   one to update).

### Add a new HTTP route

Add a router file in `apps/api/routers/`, mount it in
`apps/api/main.py`. Use `AppStateDep` for storage clients. See
`apps/api/routers/status.py` for a minimal pattern.

### Run a single ingestion against the fixture

```bash
.venv/bin/python - <<'PY'
import asyncio
from pathlib import Path
from core.parsing import FileWalker, PythonParser
from core.ingestion import GraphBuilder

walk = FileWalker().walk(Path("tests/fixtures/sample_repo"), repo_id="acme")
units = []
for ref in walk.files:
    units.extend(PythonParser().parse_file(
        source=Path("tests/fixtures/sample_repo", ref.path).read_text(),
        repo_id="acme", file_path=ref.path, commit_sha="demo",
    ))
print(f"{len(units)} units, {len(walk.files)} files")
res = GraphBuilder().build(units)
print(f"{len(res.nodes)} nodes, {len(res.edges)} edges")
PY
```

## Debugging tips

### Determinism regression

If a test that used to pass now fails an "identical across runs"
assertion:

1. Re-run the failing test twice — confirm it fails the same way.
2. Print the diverging field and check for a missing `sorted()` /
   `tuple()` / `now`-as-data path.
3. Search the diff for `random`, `uuid.uuid4()` (allowed in MCP request
   IDs only), `datetime.now()` outside lifespan / context construction.

### "Where did this audit event come from?"

Every event includes `phase`, `action`, `actor`, `entity_id`,
`tenant_id`, `before_hash`, `after_hash`. Search the source for the
event name (e.g. `mcp_tool_call`) and the call sites are the only
emitters per the `emit_phase{N}_event` discipline.

### "Why did retrieval return 0 hits?"

1. Does the repo have units? `SELECT count(*) FROM ingestion_units WHERE repo_id = 'X';`
2. Is the Qdrant collection populated? Check `units_collection`
   in the ingest response — it should match the retrieve path.
3. Inspect the `RetrieveResponse` payload — `vector_hits` /
   `graph_hits` / `metadata_hits` per channel and `failed_channels`.

### Hot-reload doesn't pick up a change

`uvicorn --reload` watches `*.py`. Static UI files under
`apps/ui/static/` aren't watched — refresh the browser. Next.js
dev server (`ui/`) hot-reloads automatically.

## Coverage of the test types

| Suite | Counts | Anchors |
|---|---|---|
| Schema unit tests | ~30 | `test_*_schema.py`, `test_compression.py` |
| Storage repo tests | ~25 | `test_postgres_repo.py`, `test_neo4j_repo.py`, `test_qdrant_repo.py` |
| Pipeline / parsing | ~30 | `test_python_parser.py`, `test_pipeline.py`, `test_graph_builder.py` |
| Retrieval + ranking | ~50 | `test_retrievers.py`, `test_ranking.py`, `test_context.py` |
| MCP | ~30 | `test_mcp_executor.py`, `test_mcp_router.py`, `test_mcp_tools.py` |
| Lifecycle (Phase 6) | ~33 | `test_lifecycle_*.py` |
| Phase 7 (scale) | ~70 | `test_phase7_*.py` |
| Phase 8 (governance) | ~75 | `test_phase8_*.py` |
| Phase 9 (deploy) | ~30 | `test_phase9_*.py` |
| Golden gates | ~30 | `test_golden_phase{2,3,4,5,6,7,8}.py` |

Total: ~442 tests, ~0.7s wall-clock, fully deterministic.

---

Next: [06 — Configuration](06_CONFIGURATION.md)
