# 27 · Feature Matrix — one engine, three deployment postures

← back to [index](00_INDEX.md) · related: [01_OVERVIEW](01_OVERVIEW.md), [21_DEPLOYMENT](21_DEPLOYMENT.md), [22_SECURITY_AND_ACCESS_CONTROL](22_SECURITY_AND_ACCESS_CONTROL.md)

Memory-CL is **one engine with three deployment postures**. The same
deterministic core — multi-language ingestion, a typed knowledge graph,
hybrid retrieval with an auditable ranking formula, and 14 MCP tools —
serves a solo developer pointing Claude at a side project, a small team
sharing a memory server on a LAN VM, and a company that needs hardened
containers, health gates, and a tamper-evident audit trail. Nothing below
is aspirational marketing: **every feature in this document was verified
against the code on 2026-06-14** (identity milestone updated 2026-06-14) (file paths inline), and each carries an
honest maturity label. Where the engine ships scaffolding instead of a
finished feature, the ledger says so.

Maturity vocabulary used throughout:

| Label | Meaning |
|---|---|
| **stable** | Wired into the request path, tested, running in a real deployment |
| **functional** | Works end-to-end but has a known limitation worth reading |
| **scaffolding** | Real code + tests exist, but nothing in the runtime invokes it |
| **planned** | Named in code or docs; no implementation yet |

---

## Summary table

✓ = core for this audience · ○ = useful · — = overkill

| Feature | Indie dev | Small team | Production | Maturity |
|---|:-:|:-:|:-:|---|
| Multi-language code ingestion (7 langs) | ✓ | ✓ | ✓ | stable |
| Markdown/text docs ingestion | ✓ | ✓ | ✓ | stable |
| Deterministic unit extraction + stable ids | ✓ | ✓ | ✓ | stable |
| Idempotent, incremental re-ingest | ✓ | ✓ | ✓ | stable |
| Per-repo isolation (`repo_id` scoping) | ○ | ✓ | ✓ | stable |
| Knowledge graph (Neo4j, typed edges, EDGE_RULES) | ✓ | ✓ | ✓ | stable |
| Whole-repo graph endpoint + viewer | ○ | ✓ | ○ | functional |
| Repo discovery + qname autocomplete | ○ | ✓ | ✓ | stable |
| Embeddings — OpenAI **or** on-device local (fastembed) | ○ | ✓ | ✓ | functional |
| Re-embed backfill (`/ingest/reembed`) | ○ | ✓ | ✓ | functional |
| Hybrid retrieval (vector + graph + keyword) | ✓ | ✓ | ✓ | stable |
| Fixed-weight ranking + per-result breakdown | ✓ | ✓ | ✓ | stable |
| Explainability (served weights, channel counts) | ○ | ✓ | ✓ | stable |
| Module summaries / dense compression | ○ | ○ | ○ | functional |
| 14 MCP tools | ✓ | ✓ | ✓ | stable |
| Native MCP server (SSE + streamable HTTP) | ✓ | ✓ | ✓ | functional |
| MCP stdio bridge | ✓ | ○ | — | functional |
| REST API | ○ | ✓ | ✓ | stable |
| Python SDK (`AsyncMemoryClient`) | ○ | ✓ | ✓ | stable |
| TypeScript SDK (embedded in UI) | — | ○ | ○ | functional |
| `memcl` CLI (canonical JSON) | ✓ | ✓ | ✓ | stable |
| Web UI (10 pages, mobile nav) | ○ | ✓ | ✓ | functional |
| Session memory (`update_memory` → Redis) | ○ | ○ | ○ | functional |
| API-key auth on mutations + MCP | — | ✓ | ✓ | functional |
| Human identity + RBAC (local auth, OIDC, teams, per-repo grants) | — | ✓ | ✓ | functional |
| Hash-chained audit log + `/audit/verify` | — | ○ | ✓ | functional |
| Snapshot / replay | — | ○ | ✓ | functional |
| Health surface + `/status` boot stages | ○ | ✓ | ✓ | stable |
| Boot orchestration + strict env contract | — | ○ | ✓ | stable |
| Safe-mode degradation states | — | — | ○ | scaffolding |
| Observability (OTEL + structlog) | — | ○ | ✓ | stable |
| Hardened production Docker stack | — | ○ | ✓ | stable |
| Golden integration tests (real stores) | ○ | ○ | ✓ | stable |
| Lifecycle (decay / refresh / compaction) | — | — | ○ | scaffolding |
| Distributed scale (sharding, workers) | — | — | ○ | scaffolding |
| Governance (tenants, policies, access control) | — | — | ✓ | scaffolding |
| Lite mode (no-Docker, `pip install` + `memcl serve`) | ✓ | ○ | — | functional |

---

## Tier 1 — Indie devs

**Who this is for.** A solo developer who wants their coding agent to
stop re-reading the whole repo every session. One machine, one or two
repos, probably no API key discipline, definitely no ops team.

**Recommended setup.**
**Lite mode** (shipped): `pip install memory-cl && memcl serve` runs the
whole engine on localhost with embedded SQLite/numpy/Python backends — no
Docker, no databases, on-device embeddings (no API key). Data lives in
`~/.memcl`. For multi-repo / heavier use, the server stack:
`docker compose up -d` (Postgres + Qdrant + Neo4j + Redis + API + UI),
then `memcl ingest /path/to/repo --repo-id my-repo`. With `MCP_API_KEY`
unset, auth is a no-op (`apps/mcp/auth.py`) — fine on localhost only.

### Memory & ingestion
- **Multi-language code ingestion** — Python (native AST,
  `core/parsing/python_parser.py`), JavaScript, TypeScript, C#, Go, Java,
  Rust (tree-sitter, `core/parsing/languages/`). *Why it matters:* your
  agent gets real symbols — functions, classes, imports — not text chunks,
  in whatever language your project mixes. — **stable**
- **Docs ingestion** — `.md` / `.mdx` / `.rst` split into heading
  sections, relative links become graph edges; `.txt` too
  (`core/parsing/doc_parser.py`, `file_walker.py`). Tooling dirs
  (`.claude/`, `.github/`, `.planning/`, …) excluded by default. *Why:*
  your README and design notes answer "why" questions code can't. — **stable**
- **Deterministic extraction + stable ids** — `stable_unit_id()` in
  `schemas/ingest.py` derives ids from `repo/file/qname`; same source →
  same ids, byte-identical, every run. *Why:* references your agent saved
  yesterday still resolve today. — **stable**
- **Idempotent, incremental re-ingest** — `core/ingestion/pipeline.py`
  compares `source_sha` per unit, skips unchanged files, surgically
  deletes + rewrites changed ones. *Why:* re-ingesting after every commit
  is cheap, so you actually do it. — **stable**

### Retrieval
- **Hybrid retrieval** — vector + graph + keyword (Postgres `ILIKE` over
  qname/name/docstring/signature, `core/retrieval/metadata_retriever.py`)
  channels run in parallel; one channel failing never kills the query
  (`hybrid_retriever.py`). *Why:* works even before you configure
  embeddings — keyword + graph carry the load. — **stable**
- **Pluggable embeddings** — OpenAI (`text-embedding-3-small`, 1536-dim,
  `core/embeddings/openai_embedder.py`) **or** on-device local (fastembed
  `bge-small`, 384-dim, no API key, `core/embeddings/local_embedder.py`),
  chosen by `embedding_mode` at runtime. Without either, the other channels
  still carry retrieval. — **functional**

### Graph
- **Typed knowledge graph** — Neo4j nodes/edges validated fail-fast
  against `EDGE_RULES` (`schemas/graph.py`,
  `core/ingestion/graph_builder.py`). *Why:* "what calls this?" answered
  structurally, not by grep. — **stable**

### Integration
- **14 MCP tools** — agent-first: `search_code`, `read_unit`, `read_file`,
  `explore`, `find_symbol`, `list_repos`, `repo_overview`, plus
  `get_context`, `get_module_summary`, `get_related_components`, `get_risks`,
  `query_graph`, `ingest_repository`, `update_memory`
  (`apps/mcp/registry.py`). *Why:* this is the whole point — Claude/Cursor/Zed
  call these directly. — **stable**
- **Native MCP server + stdio bridge** — SSE at `/mcp/sse`, streamable
  HTTP at `/mcp/http` (`apps/mcp/native_transport.py`);
  `scripts/mcp_bridge.py` for stdio-only clients. — **functional**
- **`memcl` CLI** — `search`, `read`, `explore`, `ingest`, `repos`,
  `freshness`, `token`, `serve`, `setup`, `snapshot`, `status`, … (v1
  `query`/`graph` kept as aliases); canonical sorted-key JSON output for
  stable diffs (`apps/cli/main.py`). — **stable**

### Operations
- **Web UI** (light/emerald command center) — Command Center, Ask
  (search), Graph, Read, Repositories, Activity, Metrics, Health, Settings
  (keys/tokens/embeddings/webhook), Setup wizard. — **functional**
- **Golden test suite** — `pytest tests/` passes with no Docker daemon;
  golden integration tests skip cleanly when stores are absent. — **stable**

---

## Tier 2 — Small teams

**Who this is for.** 2–10 people sharing one Memory-CL instance on a LAN
VM or small cloud box: several repos, several agents, one shared API key,
someone informally "the ops person".

**Recommended setup.** The production compose stack on a single VM:
`docker compose -f docker-compose.production.yml up -d`. Set
`MCP_API_KEY` (boot **fails** in production without it —
`Settings._enforce_environment_contract`). This is exactly the verified
homelab posture: the live instance at the reference deployment serves 4
repos across 9 languages.

Everything in Tier 1, plus:

### Memory & ingestion
- **Per-repo isolation** — every query, graph traversal, and vector
  search is `repo_id`-scoped; each repo gets its own Qdrant collection
  (`repo_{repo_id}`, `apps/api/routers/retrieve.py:122`). *Why:* the
  frontend repo's noise never pollutes the backend repo's answers. — **stable**
- **Repo discovery** — `GET /repos` (units/files/languages per repo),
  `GET /repos/{id}/qnames` powering debounced autocomplete in the UI
  (`ui/components/QnameInput.tsx`). *Why:* new teammates can see what's
  ingested without asking. — **stable**
- **Re-embed backfill** — `POST /ingest/reembed` upgrades
  placeholder vectors to real ones after you add an embedding key;
  API-key-gated because it spends provider money. — **functional**

### Retrieval
- **Explainable ranking** — every result carries a
  `breakdown: RankingFeatures` (`schemas/retrieval.py:79`); the mandated
  weights (semantic 0.35 / graph 0.25 / recency 0.20 / importance 0.15 /
  feedback 0.05) are served live in `/status.feature_weights`. *Why:*
  "why did it return *that*?" has an answer you can paste in Slack. — **stable**
- **Module summaries** — `get_module_summary` serves dense per-module
  digests via `core/summarization/ModuleSummarizer`. — **functional**

### Graph
- **Whole-repo graph** — `GET /repos/{id}/graph` + the universal graph
  viewer (`ui/components/RepoGraphViewer.tsx`). *Why:* onboarding — see
  the shape of a codebase before reading it. — **functional**

### Integration
- **REST API** — ~14 endpoints across retrieve / ingest / repos / mcp /
  audit / snapshot / health / status, all `X-Request-ID`-correlated
  (`apps/api/middleware.py`). — **stable**
- **Python SDK** — `sdk/client.py::AsyncMemoryClient`, typed, maps 1:1
  to CLI subcommands. — **stable**
- **TypeScript SDK** — `ui/lib/api.ts::AsyncMemoryClient`, fully typed
  against `ui/lib/types.ts`; lives inside the UI package, **not published
  as a standalone npm package**. — **functional**
- **Session memory** — `update_memory` appends to a TTL'd Redis list per
  `(repo, session)` (`core/mcp/tools/memory_tool.py`). *Why:* agents can
  leave notes for their next run. — **functional**

### Operations
- **API-key auth** — single shared `MCP_API_KEY` (X-API-Key or Bearer)
  gates MCP execution, `/ingest`, and `/ingest/reembed`
  (`apps/mcp/auth.py`, `native_auth.py`). *Honest limit:* one key for
  all agents; per-person human identity now ships in Tier 3 (see below). — **functional**
- **Health + status surface** — `/health/live`, `/health/ready`,
  `/health/dependencies`, and `/status` with 8 named boot stages, safe-mode
  view, feature flags, and served ranking weights. *Why:* "is it down or
  is it me?" answerable in one curl. — **stable**
- **Web UI, full surface** — 10 pages (`ui/app/`): dashboard, retrieve,
  graph, ingest, status, audit, snapshot (build ×2 + client-side diff +
  replay), mcp, tool-runner, home — with mobile nav and a command palette
  (`ui/components/nav/`). — **functional**

---

## Tier 3 — Production / companies

**Who this is for.** Organizations that need the memory engine to survive
an audit conversation: hardened containers, observability, tamper-evident
logs, reproducibility guarantees, and an unsentimental view of what is
and isn't enforced yet.

**Recommended setup.** `docker-compose.production.yml` behind a reverse
proxy that terminates TLS and adds per-tenant identity. Strict bootstrap
on (the default): any degraded boot stage stops the rollout.

Everything in Tiers 1–2, plus:

### Operations & hardening
- **Hardened container stack** — `Dockerfile.production` is multi-stage,
  non-root (uid 1000), tini PID 1, lockfile-strict; compose services run
  `read_only: true` with `cap_drop` and ulimits, and the boot order is
  health-gated. *Why:* passes the platform team's checklist on day one. — **stable**
- **Boot orchestration + env contract** — 8-stage boot gate
  (storage → schema → graph/vector → ingestion → retrieval → MCP →
  audit → API, `apps/api/bootstrap.py`); production boot refuses sentinel
  passwords and missing `MCP_API_KEY` (`core/config.py`). — **stable**
- **Observability** — OTEL tracers/meters bootstrapped in
  `core/observability/_otel.py`; structured `structlog` events on every
  pipeline stage; `X-Request-ID` propagated UI → API → logs → traces. — **stable**
- **Golden integration tests against real stores** —
  `tests/integration/test_storage_golden.py` ingests fixtures through the
  same client construction the API lifespan uses, against real
  containerized Postgres/Qdrant/Neo4j/Redis — added specifically because
  six wire-level driver bugs slipped past mocked tests. — **stable**

### Governance & reproducibility
- **Hash-chained audit log** — every MCP tool call (success *and*
  failure) appends a SHA-256-chained entry (`apps/mcp/router.py`,
  `core/governance/audit_logger.py`, `infra/audit/immutable_log_store.py`);
  `/audit/tail` reads it, `/audit/verify` re-walks the chain. *Honest
  limit:* the chain is **in-memory per process — it resets on every
  restart**. A persistent `JsonlFileAuditSink` exists in code but is not
  wired into the lifespan. — **functional**
- **Snapshot / replay** — `POST /snapshot/build` produces a
  content-derived snapshot id (same inputs → same id);
  `POST /snapshot/replay` verifies byte-equality via deterministic JSON
  hashing (`apps/api/routers/snapshot.py`, `core/reproducibility/`).
  *Honest limit:* the served snapshot is a **boot snapshot** (MCP registry
  + schema version + state token) — it does not capture graph or vector
  state; the deeper `SystemSnapshotBuilder` exists for callers that
  project state themselves. — **functional**
- **Determinism as a contract** — sorted walks, content-hash ids,
  caller-supplied clocks, canonical JSON everywhere; pinned by the
  per-phase golden tests (`tests/test_golden_phase*.py`). — **stable**

### Identity & access control
- **Human identity + RBAC** — local password auth (Phase 1), OIDC/OAuth federation (Phase 2),
  and teams + per-repo grants (Phase 3) are all shipped and enforced on the human request
  path. `RepoAccessResolver` evaluates org role → team grants → direct grants on every
  human-path repo endpoint. `GET /repos` is filtered; ungranted repos return 403. Cross-org
  isolation is enforced. Agents (API tokens) are org-scoped with full access to their org's
  repos — not per-repo — by design. Auth is a no-op when unconfigured (backward compatible
  with single-org or dev deployments). — **functional**
- **Governance policy engine** — `TenantManager`, `AccessControl`, and `PolicyEngine`
  (`core/governance/`) are real, tested library code but **nothing in `apps/` imports them** —
  they are not on the live request path. The policy-engine layer beyond RBAC remains
  library-only. — **scaffolding**

### Scale & multi-tenancy (read this section carefully)
- **Distributed scale** — shard routers, worker pool, backpressure,
  rate limiting, batching (`core/scaling/`, `infra/distributed/`,
  `core/performance/`) exist with golden tests, but the production
  `worker` container runs `sleep infinity`
  (`docker-compose.production.yml:139`) and the shard routers are only
  exercised by a boot probe. Single-process serving is the real posture. — **scaffolding**
- **Lifecycle (decay / refresh / compaction)** — relevance scoring,
  decay engine, compactors (`core/lifecycle/`) are implemented and tested
  as a library; no scheduler, endpoint, or worker invokes them in the
  runtime. — **scaffolding**
- **Safe-mode states** — `read_only` / `mcp_disabled` / `retrieval_only`
  are modeled, set at boot, and reported in `/status`
  (`core/safety/safe_mode.py`), but **no middleware or router refuses
  requests based on the mode** — it is a signal, not a brake. — **scaffolding**

---

## Maturity ledger

| # | Feature | Maturity | Evidence |
|---|---|---|---|
| 1 | Multi-language code ingestion (Py/JS/TS/C#/Go/Java/Rust) | stable | `core/parsing/file_walker.py:63`, `core/parsing/languages/` |
| 2 | Docs ingestion (`.md .mdx .rst .txt`, links → edges) | stable | `core/parsing/doc_parser.py` |
| 3 | Deterministic unit extraction + stable ids | stable | `schemas/ingest.py::stable_unit_id` |
| 4 | Idempotent, incremental re-ingest (`source_sha`) | stable | `core/ingestion/pipeline.py:343-384` |
| 5 | Per-repo isolation (scoping + per-repo collections) | stable | `apps/api/routers/retrieve.py:122` |
| 6 | Knowledge graph + EDGE_RULES validation | stable | `schemas/graph.py:48`, `core/ingestion/graph_builder.py:155` |
| 7 | Whole-repo graph endpoint + UI viewer | functional | `apps/api/routers/repos.py:118`, `ui/components/RepoGraphViewer.tsx` |
| 8 | Repo discovery + qname autocomplete | stable | `apps/api/routers/repos.py`, `ui/components/QnameInput.tsx` |
| 9 | Semantic embeddings (OpenAI, optional, incremental) | functional | `core/embeddings/openai_embedder.py` — OpenAI only |
| 10 | Re-embed backfill | functional | `apps/api/routers/ingest.py:157` |
| 11 | Hybrid retrieval (3 channels, failure-isolated) | stable | `core/retrieval/hybrid_retriever.py` |
| 12 | Fixed-weight ranking + breakdown | stable | `core/ranking/feature_weights.py`, `schemas/retrieval.py:79` |
| 13 | Explainability (served weights, channel counts) | stable | `/status.feature_weights`, `RetrieveResponse` |
| 14 | Dense compression + module summaries | functional | `core/compression/`, `core/mcp/tools/context_tool.py:22` |
| 15 | 14 MCP tools | stable | `apps/mcp/registry.py:23-29` |
| 16 | Native MCP server (SSE + streamable HTTP) | functional | `apps/mcp/native_transport.py` |
| 17 | MCP REST surface (`/mcp/tools`) | stable | `apps/mcp/router.py` |
| 18 | MCP stdio bridge | functional | `scripts/mcp_bridge.py`, `tests/test_mcp_bridge.py` |
| 19 | REST API (~14 endpoints) | stable | `apps/api/routers/` |
| 20 | Python SDK | stable | `sdk/client.py` |
| 21 | TypeScript SDK | functional | `ui/lib/api.ts` — embedded, not packaged |
| 22 | `memcl` CLI (7 subcommands) | stable | `apps/cli/main.py:132-171` |
| 23 | Web UI (10 pages, mobile nav, command palette) | functional | `ui/app/`, `ui/components/nav/` |
| 24 | Session memory (Redis, TTL, append-only) | functional | `core/mcp/tools/memory_tool.py` |
| 25 | API-key auth (mutations + MCP) | functional | `apps/mcp/auth.py` — single shared key |
| 26 | Hash-chained audit + verify | functional | `apps/mcp/router.py:75`, in-memory; resets on restart |
| 27 | Snapshot / replay | functional | `apps/api/routers/snapshot.py` — boot snapshot only |
| 28 | Health surface + `/status` boot stages | stable | `apps/api/routers/health.py`, `status.py` |
| 29 | Boot orchestration + strict env contract | stable | `apps/api/bootstrap.py`, `core/config.py` |
| 30 | Observability (OTEL + structlog + request ids) | stable | `core/observability/_otel.py`, `apps/api/middleware.py` |
| 31 | Hardened production Docker stack | stable | `Dockerfile.production`, `docker-compose.production.yml` |
| 32 | Golden integration tests (real stores) | stable | `tests/integration/test_storage_golden.py` |
| 33 | X-Request-ID correlation across surfaces | stable | `apps/api/middleware.py`, `ui/lib/api.ts` |
| 34 | Feature flags surfaced in `/status` | scaffolding | `core/safety/feature_flags.py` — no engine code consults them |
| 35 | Safe-mode degradation states | scaffolding | `core/safety/safe_mode.py` — reported, never enforced |
| 36 | Lifecycle (decay / refresh / compaction) | scaffolding | `core/lifecycle/` — not invoked by runtime |
| 37 | Distributed scale (shards, workers, backpressure) | scaffolding | worker = `sleep infinity`; boot-probe only |
| 38 | Governance policy engine (tenant / policy / DENY rules) | scaffolding | `core/governance/` — library only, not imported by `apps/` |
| 39 | Integrity + diagnostics validators | functional | `core/integrity/` — exercised at boot stage 3 |
| 40 | Human identity + RBAC (local auth + OIDC + teams + per-repo grants) | functional | `apps/api/routers/auth*.py`, `orgs.py`, `core/auth/repo_access.py` |
| 41 | Persistent audit sink wiring | planned | `JsonlFileAuditSink` exists, unwired |
| 42 | Voyage embedder | planned | `core/embeddings/embedder.py:9` — name only |
| 43 | Lite mode (no-Docker, `pip install` + `memcl serve`) | functional | `core/config.py` MODE/lite_data_dir, `storage/lite/`, `apps/cli/main.py` serve, tests `test_lite_*.py` |
| 44 | Worker queue execution (Phase 11 batch ingest) | planned | `docs/14` "provisioned for Phase-11" |

**Tally: 44 features — 20 stable · 15 functional · 6 scaffolding · 3 planned.** (Lite mode + full identity milestone shipped; see also embeddings, freshness, named API tokens.)

---

## Appendix — stale documentation claims (catalog only; not fixed here)

Claims found in existing docs/README that disagree with the code as of
2026-06-12. Per house rule: *the code wins.*

1. **`README.md:133-136`** — "Invoke an MCP tool directly:
   `memcl tool query_graph …`". There is **no `tool` subcommand** in
   `apps/cli/main.py` (subcommands: ingest, reembed, query, graph,
   snapshot, replay, status). Use `memcl graph` or `POST /mcp/tools/{name}`.
2. **`README.md:192-198`** — repository-structure tree describes
   `core/parsing/` as Python + "TreeSitterParser (JS/TS)" only; omits
   `doc_parser.py` and the `languages/` package (C#, Go, Java, Rust).
3. **`README.md:36,177`** — "Safe-mode states … for graceful degradation"
   overstates: modes are defined and *reported* (`/status`) but no request
   path enforces them (no consumer of `SafeModeController` outside
   `health.py`/`status.py`/lifespan).
4. **`README.md:236`** — "Status — Production-deploy ready (**not
   deployed**)". A production deployment has been live on the homelab VM
   since 2026-05 (4 repos, 9 languages served at `/repos`).
5. **`ARCHITECTURE.md:59`** — "source → IngestionUnit list (Python via
   AST; JS/TS via tree-sitter)" — omits C#/Go/Java/Rust and doc parsing.
6. **`docs/02_ARCHITECTURE.md:90`** — "parsing source → IngestionUnit
   (Python/JS/TS)" — same staleness as above.
7. **`docs/01_OVERVIEW.md:17`** — "a Python SDK + `memcl` CLI (the
   developer surface)" — omits the TypeScript SDK (`ui/lib/api.ts`),
   which `docs/20_SDK_GUIDE.md` documents.
8. **`docs/22_SECURITY_AND_ACCESS_CONTROL.md`** — Partially resolved by Phase 3: per-repo RBAC
   (`RepoAccessResolver`) is now on the human request path. `TenantManager`, `AccessControl`,
   and `PolicyEngine` (`core/governance/`) remain library-only — the policy-engine layer is
   still not imported by `apps/`. The "Tenant isolation — `TenantManager.assert_owns_repo`
   everywhere" claim remains stale for the agent/policy path.
9. **`SECURITY.md:51` (auth table)** — "`POST /ingest` | None by
   default". Stale: `apps/api/routers/ingest.py:103,165` gate both
   `/ingest` and `/ingest/reembed` with the same `ApiKeyDep` as MCP.
10. **`docs/14_DISTRIBUTED_SYSTEM.md:5`** — "Phase 7 turns the engine
    into a horizontally scalable runtime". The production worker runs
    `sleep infinity` and shard routers are only boot-probed; the runtime
    is single-process.
11. **`docs/14_DISTRIBUTED_SYSTEM.md:24`** — documents the sharded
    vector collection name as `repo:{repo_id}::s{shard_idx}`. The actual
    serving path uses `repo_{repo_id}`
    (`apps/api/routers/ingest.py:128`, `retrieve.py:122`) — and Qdrant
    rejects `:` in collection names (the bug class fixed in the 2026-05
    rollout).
12. **`docs/13_MEMORY_EVOLUTION.md:5`** — "turns the static memory
    engine into a living one" — no scheduler, endpoint, or worker invokes
    `core/lifecycle/` at runtime; it is library + tests.
13. **`docs/16_AUDIT_AND_GOVERNANCE.md`** — documents
    `JsonlFileAuditSink.replay()` for backup/restore but never states
    that the **deployed default is in-memory** (`apps/api/lifespan.py:156`
    constructs `AuditLogger()` bare → `InMemoryAuditSink` +
    in-memory chain); the audit chain resets on every process restart.
14. **`core/safety/feature_flags.py:50-52`** (served at `/status`, echoed
    by `docs/06_CONFIGURATION.md:115-117`) — flag description "Apply
    Phase-3 dense compression on retrieval" is misleading: no retrieval
    code consults `enable_context_compression` (nor
    `enable_graph_ranking` / `enable_incremental_indexing`); those
    behaviors are unconditional or absent regardless of the flag.
15. **`core/embeddings/embedder.py:9`** — `EmbedderName` advertises
    `"voyage"`; no Voyage embedder implementation exists.
