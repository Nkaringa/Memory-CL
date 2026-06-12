# UI Tier-1: repo discovery + honesty pass — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** A first-time user can discover ingested repos from every form, understands why vector results are empty, and never hits the "acme" dead end.

**Architecture:** One new read-only backend endpoint (`GET /repos`) backed by a Postgres aggregate; a seed-metadata fix so `query_graph` depth-0 candidates carry name/kind; a shared `RepoSelect` UI component wired into all four form surfaces; an honesty pass over labels; first-run onboarding; ingest timeout + error-detail fixes.

**Tech stack:** FastAPI + SQLAlchemy text() (CAST lesson from B14/B15 applies), Next.js 14 app router + React Query.

**Source audits:** two audit reports (2026-06-12) — findings C1-C7, I1-I8 referenced below. Verification for UI = `npm run build` typecheck (no UI test infra exists); backend = pytest.

---

### Task 1: Backend — `GET /repos` + seed metadata fix

**Files:** `storage/postgres_repo.py`, `storage/repositories.py` (protocol), `apps/api/routers/repos.py` (new), `apps/api/main.py`, `storage/neo4j_repo.py` (+`get_node`), `core/retrieval/graph_retriever.py` (seed hydration), `core/mcp/tools/graph_tool.py` if needed, tests.

1. `PostgresUnitsRepository.list_repos()` → `SELECT repo_id, count(*) AS units, count(DISTINCT file_path) AS files, array_agg(DISTINCT language) AS languages, max(commit_sha) ... GROUP BY repo_id ORDER BY repo_id`. Return list of dicts/dataclass. Mind asyncpg typing lessons (no CAST needed for pure selects).
2. New router `apps/api/routers/repos.py`: `GET /repos` → `{"repos": [{"repo_id", "units", "files", "languages"}]}`, unauthenticated (read-only, same posture as /status). Register in main.py.
3. `Neo4jGraphRepository.get_node(node_id) -> GraphNode | None` — `MATCH (n {node_id: $node_id}) RETURN n` + existing `_record_to_node`.
4. `GraphRetriever.search`: hydrate seed nodes via `get_node` (guard `hasattr` for fake sources in tests) so depth-0 candidates carry qualified_name/kind/file_path (fixes the null-seed finding). Swallow per-seed lookup failures (warning event) — degraded, not fatal.
5. Tests: mocked-repo tests for list_repos SQL shape + router response; get_node; retriever seed hydration (seed candidate has qualified_name when source provides get_node).

### Task 2: UI — api client + RepoSelect component

**Files:** `ui/lib/api.ts`, `ui/lib/types.ts`, `ui/components/RepoSelect.tsx` (new).

1. `listRepos()` in api.ts (GET /api/repos), `RepoInfo` type.
2. `RepoSelect` — labeled select fed by `useQuery(["repos"])`: options = repo_ids (with unit counts in the option label), controlled `value/onChange`, graceful degradation to a free-text input when the endpoint errors or returns empty (with placeholder "repo id (e.g. my-repo)"), never hardcodes "acme".

### Task 3: UI — wire RepoSelect everywhere, kill "acme"

**Files:** `ui/components/QueryBox.tsx`, `ui/app/graph/page.tsx`, `ui/app/ingest/page.tsx`, `ui/app/snapshot/page.tsx`, `ui/components/ToolRunner.tsx`.

1. Replace every free-text repo_id input + `"acme"`/`"acme-corp"` default with `RepoSelect` (default = first repo from the list, else empty + required validation).
2. ToolRunner templates: substitute the first available repo_id into example payloads at render time (fall back to `"<repo-id>"` placeholder text); replace `pkg.utils.add` example seeds with a note "use a qualified_name from your repo (see /retrieve)".
3. Graph page: debounce depth-slider re-queries (300ms) (audit I5).

### Task 4: UI — honesty pass

**Files:** `ui/app/retrieve/page.tsx`, `ui/components/QueryBox.tsx`, `ui/app/dashboard/page.tsx`, `ui/app/ingest/page.tsx`, `ui/components/ExplainPanel.tsx`, `ui/components/nav/Sidebar.tsx`, `ui/app/graph/page.tsx`, `ui/components/GraphViewer.tsx`.

1. Remove the no-op channel toggles from QueryBox (C1); replace with read-only channel-hit badges on results.
2. Retrieve page: amber info banner — "Semantic vectors are not enabled yet (Phase-3 pending): the vector channel returns 0 hits by design. Graph + keyword channels are live." (C2)
3. Phase wording: dashboard phase list marks Phase-3 as "pending"; ingest page drops "Phase-3 compression"/"embed" stage claims (placeholder payloads stated honestly); ExplainPanel drops "cosine similarity over the dense index" wording (C2/M7).
4. Sidebar: remove "production-ready" (C7).
5. Graph page: canvas label "Reachability view — drawn edges are seed→node projections, not literal graph edges" (C3); when result is seed-only (1 candidate at depth 0), inline notice "Only the seed returned — the node may have no edges at this depth." (C6)

### Task 5: UI — first-run onboarding + ingest timeout + error detail

**Files:** `ui/app/page.tsx`, `ui/app/dashboard/page.tsx`, `ui/app/retrieve/page.tsx`, `ui/lib/api.ts`, `ui/app/ingest/page.tsx`, `ui/components/ui/error-state.tsx`.

1. First-run card (shared component `FirstRunCard`): when `listRepos()` returns empty — "No repositories ingested yet. Memory-CL answers from YOUR code — ingest a repo first." CTA → /ingest. Shown on /, /dashboard, /retrieve. (I1)
2. api.ts: per-call timeout override; ingest call uses 600_000 ms; on abort show "The request timed out client-side — ingestion may still be running on the server; check the Dashboard/Audit pages before retrying." (I2)
3. ErrorState: render `MemoryClientError.body.detail` when present; map 401 → "API key missing/invalid (MCP_API_KEY)"; abort → timeout wording. (I8)

### Task 6: Verification + ship

1. Backend: `.venv/bin/pytest tests/ -q`, `.venv/bin/ruff check .`
2. UI: `cd ui && npm run build` (typedRoutes + TS strict = the gate; no UI test infra exists)
3. UI production Docker build: `docker build -f ui/Dockerfile.production ui/`
4. Curl smoke on new endpoint shape via uvicorn or trust tests; push branch.

**Out of scope (Tier 2, documented in audit):** real graph edges in query_graph response, qname autocomplete, open-in-graph actions, mobile nav, ToolRunner schemas, auth on /ingest, score=0.0 ranking investigation.
