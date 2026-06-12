# Memory-CL

**A deterministic AI memory engine — same input + same state → byte-identical output.**

[![Status](https://img.shields.io/badge/status-production_ready-success)]()
[![License](https://img.shields.io/badge/license-Proprietary-lightgrey)]()
[![Python](https://img.shields.io/badge/python-3.12-blue)]()
[![Schema](https://img.shields.io/badge/schema-v1-informational)]()

> A navigable operating-system manual lives in [`docs/`](docs/00_INDEX.md).
> This README is the front door — orientation only.

---

## What Memory-CL is

Memory-CL turns a codebase into a queryable knowledge surface
(graph + vectors + canonical metadata) and exposes it to agents,
developers, and operators through HTTP, MCP, an SDK, a CLI, and a
transparency UI. Every result carries the trace that produced it.

For the longer mental model: **[docs/01_OVERVIEW](docs/01_OVERVIEW.md)**.

---

## Key capabilities

- **Multi-language ingestion** — Python (`.py`), JavaScript (`.js .mjs .cjs .jsx`), and TypeScript (`.ts .tsx .mts .cts`); declaration files (`.d.ts` / `.d.mts` / `.d.cts`) are skipped
- **Hybrid retrieval** — graph, vector, and metadata channels blended by a fixed-weight ranking formula
- **Explainable results** — every ranked entry surfaces its score breakdown + pipeline trace
- **Deterministic outputs** — pinned by snapshot + replay, byte-identical across runs
- **Agent-native** — 7 MCP tools, exposed both as a REST surface AND a real MCP-protocol server (SSE + streamable HTTP). Stdio bridge ships for clients that don't yet speak remote MCP
- **Tamper-evident audit** — hash-chained governance ledger with `/audit/verify`
- **Operationally honest** — `/health/live`, `/health/ready`, `/health/dependencies`, `/status` give an unflinching live view
- **Safe-mode states** — discrete failure modes (`read_only` / `mcp_disabled` / `retrieval_only`) for graceful degradation
- **Production-hardened** — reproducible images, lockfile-strict installs, non-root containers, strict env validation

---

## Architecture (high-level)

```text
                  CLI / SDK / UI
                        ↓
                  API + MCP Layer
                        ↓
       Retrieval + Ranking + Memory Engine
                        ↓
        Graph + Vector + Metadata Storage
```

Layered, single-direction dependencies. Storage is hidden behind the
engine; the engine is hidden behind the HTTP+MCP layer; clients never
touch backends directly.

Deep dive: **[ARCHITECTURE.md](ARCHITECTURE.md)** ·
**[docs/02_ARCHITECTURE](docs/02_ARCHITECTURE.md)** ·
**[docs/03_DATA_FLOW](docs/03_DATA_FLOW.md)**.

---

## What's inside

| Capability | Reference |
|---|---|
| Foundation — schemas, storage clients, FastAPI skeleton | [docs/02](docs/02_ARCHITECTURE.md) |
| Code ingestion — walk → parse → graph + vectors | [docs/11](docs/11_GRAPH_SYSTEM.md) |
| Compression — dense encoding + summaries | [docs/12](docs/12_EMBEDDINGS_AND_COMPRESSION.md) |
| Hybrid retrieval + fixed-weight ranking | [docs/09](docs/09_RETRIEVAL_SYSTEM.md), [docs/10](docs/10_RANKING_ENGINE.md) |
| MCP tools — agent surface | [docs/08](docs/08_MCP_TOOLING.md) |
| Lifecycle — decay / refresh / compaction | [docs/13](docs/13_MEMORY_EVOLUTION.md) |
| Distributed scale — sharding, cache, backpressure | [docs/14](docs/14_DISTRIBUTED_SYSTEM.md) |
| Governance — audit chain, snapshot, replay | [docs/16](docs/16_AUDIT_AND_GOVERNANCE.md), [docs/17](docs/17_SNAPSHOT_AND_REPLAY.md) |
| Production deployment — boot gate, safe mode, `/status` | [docs/21](docs/21_DEPLOYMENT.md), [DEPLOYMENT](DEPLOYMENT.md) |
| Production hardening — lockfile, strict env, modular runbook | [DEPLOYMENT](DEPLOYMENT.md), [RUNBOOK](RUNBOOK.md) |

---

## Quickstart

```bash
# 1. Bring up the dev stack (postgres, qdrant, neo4j, redis, api)
docker compose up -d

# 2. Sanity check — the API is alive
curl -fsS http://localhost:8000/health/ready | jq .

# 3. Ingest a repo
memcl ingest /absolute/path/to/your/repo --repo-id my-repo

# 4. Query it
memcl query "what does the auth flow do?" --repo-id my-repo

# 5. Open the transparency UI
open http://localhost:3000
```

Step-by-step setup with prerequisites:
**[docs/04_INSTALLATION](docs/04_INSTALLATION.md)** ·
**[docs/05_LOCAL_DEVELOPMENT](docs/05_LOCAL_DEVELOPMENT.md)**.

---

## Documentation map

| You are… | Start here |
|---|---|
| **First-time reader** | [docs/01_OVERVIEW](docs/01_OVERVIEW.md) → [docs/02_ARCHITECTURE](docs/02_ARCHITECTURE.md) → [docs/03_DATA_FLOW](docs/03_DATA_FLOW.md) |
| **Setting up locally** | [docs/04_INSTALLATION](docs/04_INSTALLATION.md) → [docs/05_LOCAL_DEVELOPMENT](docs/05_LOCAL_DEVELOPMENT.md) → [docs/06_CONFIGURATION](docs/06_CONFIGURATION.md) |
| **Integrating an agent** | [docs/07_API_REFERENCE](docs/07_API_REFERENCE.md) → [docs/08_MCP_TOOLING](docs/08_MCP_TOOLING.md) → [docs/MCP_SERVER](docs/MCP_SERVER.md) / [docs/MCP_BRIDGE](docs/MCP_BRIDGE.md) → [docs/20_SDK_GUIDE](docs/20_SDK_GUIDE.md) |
| **Debugging a query** | [docs/09_RETRIEVAL_SYSTEM](docs/09_RETRIEVAL_SYSTEM.md) → [docs/10_RANKING_ENGINE](docs/10_RANKING_ENGINE.md) → [docs/18_UI_GUIDE](docs/18_UI_GUIDE.md) |
| **Deploying to prod** | [DEPLOYMENT](DEPLOYMENT.md) → [SECURITY](SECURITY.md) → [docs/21_DEPLOYMENT](docs/21_DEPLOYMENT.md) → [docs/15_OBSERVABILITY](docs/15_OBSERVABILITY.md) |
| **On-call / incident** | [RUNBOOK](RUNBOOK.md) → [docs/24_TROUBLESHOOTING](docs/24_TROUBLESHOOTING.md) → [docs/16_AUDIT_AND_GOVERNANCE](docs/16_AUDIT_AND_GOVERNANCE.md) |
| **Contributing code** | [docs/02_ARCHITECTURE](docs/02_ARCHITECTURE.md) → [docs/25_DESIGN_DECISIONS](docs/25_DESIGN_DECISIONS.md) → [docs/05_LOCAL_DEVELOPMENT](docs/05_LOCAL_DEVELOPMENT.md) |

Full index: **[docs/00_INDEX](docs/00_INDEX.md)** · Glossary: **[docs/26_GLOSSARY](docs/26_GLOSSARY.md)**.

---

## Common workflows

**Ingest a repository**
```bash
memcl ingest /path/to/repo --repo-id my-repo --commit-sha $(git rev-parse HEAD)
```

**Run a hybrid retrieval and inspect the score breakdown**
```bash
memcl query "where is rate limiting enforced?" --repo-id my-repo --top-k 8
```

**Invoke an MCP tool directly**
```bash
memcl tool query_graph --node "auth.middleware.verify_token" --repo-id my-repo --depth 2
```

**Build + replay a snapshot**
```bash
memcl snapshot --tenant-id acme --state-version v0
memcl replay <snapshot_id> --payload '{"a":1}' --expected '{"a":1}'
```

**Verify the audit chain**
```bash
curl -fsS http://localhost:8000/audit/verify | jq
```

More: **[docs/19_CLI_REFERENCE](docs/19_CLI_REFERENCE.md)** ·
**[docs/07_API_REFERENCE](docs/07_API_REFERENCE.md)**.

---

## UI · CLI · SDK

| Surface | What it's for | Reference |
|---|---|---|
| **Next.js UI** (`/ui`) | Transparency layer — every result carries its breakdown, every system signal has a panel | [docs/18_UI_GUIDE](docs/18_UI_GUIDE.md) |
| **`memcl` CLI** | Operator + developer console; canonical-JSON output for byte-stable diffs | [docs/19_CLI_REFERENCE](docs/19_CLI_REFERENCE.md) |
| **Python SDK** (`AsyncMemoryClient`) | Single typed entry point for agents and integrations | [docs/20_SDK_GUIDE](docs/20_SDK_GUIDE.md) |
| **MCP tools** | Seven canonical tools, schema-pinned, reproducible | [docs/08_MCP_TOOLING](docs/08_MCP_TOOLING.md) |
| **MCP server** | Native MCP-protocol server over SSE / streamable HTTP for agent clients (Claude Desktop, Cursor, Code, Zed) | [docs/MCP_SERVER](docs/MCP_SERVER.md) |
| **MCP bridge** | Local stdio adapter for clients that don't yet speak remote MCP | [docs/MCP_BRIDGE](docs/MCP_BRIDGE.md) |

All four surfaces propagate the same `X-Request-ID` so logs, traces,
and UI activity correlate across layers.

---

## Production readiness

Production-hardening highlights:

- **Reproducible images** — `Dockerfile.production` is multi-stage, non-root (uid 1000), tini PID 1, lockfile-strict (`requirements.lock.txt`)
- **Strict env validation** — `Settings._enforce_environment_contract` rejects sentinel passwords, missing `MCP_API_KEY`, and unsafe production defaults at boot
- **Expanded health surface** — `/health/live`, `/health/ready`, `/health/dependencies`, `/status` (each documented in [DEPLOYMENT](DEPLOYMENT.md#5-health-surface))
- **Discrete safe-modes** — `read_only` / `mcp_disabled` / `retrieval_only` give operators graceful degradation paths
- **Single-command stack** — `docker compose -f docker-compose.production.yml up -d` brings up api + ui + worker + postgres + qdrant + neo4j + redis with health-gated boot order
- **Modular ops docs** — [DEPLOYMENT](DEPLOYMENT.md), [RUNBOOK](RUNBOOK.md), [ARCHITECTURE](ARCHITECTURE.md), [SECURITY](SECURITY.md)

---

## Repository structure

```text
.
├── apps/
│   ├── api/           FastAPI app, routers, lifespan, middleware
│   ├── mcp/           MCP tool registry + executor
│   └── cli/           memcl CLI
├── core/
│   ├── parsing/
│   │   ├── base.py            SourceParser Protocol
│   │   ├── python_parser.py   PythonParser (AST, hard-fails on syntax error)
│   │   ├── treesitter_parser.py TreeSitterParser (JS/TS; error-tolerant)
│   │   ├── qnames.py          module_qname_from_path shared helper
│   │   └── file_walker.py     deterministic FileRef walk
│   ├── ingestion/             pipeline orchestrator + graph builder
│   └── …                      retrieval, ranking, governance, scaling, …
├── storage/           Postgres / Qdrant / Neo4j / Redis client wrappers
├── schemas/           Wire shapes shared by API + SDK + CLI
├── sdk/               AsyncMemoryClient (Python)
├── ui/                Next.js transparency UI (own Dockerfile.production)
├── infra/             Cross-cutting infra helpers
├── scripts/           boot.sh + dev tooling
├── docs/              27-file modular documentation system
├── tests/             Unit + integration suite
├── DEPLOYMENT.md      Operator green-path
├── RUNBOOK.md         Incident response
├── ARCHITECTURE.md    System topology
├── SECURITY.md        Threat model + access controls
├── Dockerfile.production
├── docker-compose.production.yml
└── requirements.lock.txt
```

The dependency rule: `schemas ← storage ← core ← apps`,
with `sdk` depending only on `schemas`. Detail in
**[docs/02_ARCHITECTURE](docs/02_ARCHITECTURE.md)**.

---

## Contributing

1. Read **[docs/25_DESIGN_DECISIONS](docs/25_DESIGN_DECISIONS.md)** before opening a PR — the "why" matters more than the "what" here.
2. Keep changes inside a single layer's responsibility (`schemas` / `storage` / `core` / `apps`). Cross-layer changes need explicit justification.
3. Run the full local stack and `pytest tests/` before pushing.
4. Determinism is a hard invariant. If your change can produce different outputs for the same input, it needs an explicit reason in the commit message.

Local dev loop: **[docs/05_LOCAL_DEVELOPMENT](docs/05_LOCAL_DEVELOPMENT.md)**.

---

## License · Status

- **Status** — Production-deploy ready (not deployed).
- **License** — Proprietary. See `pyproject.toml` for the canonical declaration.
- **Schema version** — `v1`. Wire compatibility is pinned by `schemas.base.SCHEMA_VERSION`.

For the deeper invariants and what this system explicitly is **not**,
see **[ARCHITECTURE.md §10](ARCHITECTURE.md#10-what-this-architecture-explicitly-is-not)**.
