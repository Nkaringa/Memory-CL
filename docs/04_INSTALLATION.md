# 04 · Installation

← back to [index](00_INDEX.md) · related: [05_LOCAL_DEVELOPMENT](05_LOCAL_DEVELOPMENT.md), [06_CONFIGURATION](06_CONFIGURATION.md), [21_DEPLOYMENT](21_DEPLOYMENT.md)

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.12+ | Backend (typing + asyncio features) |
| Docker + Compose | 24+ | Runs Postgres + Neo4j + Qdrant + Redis |
| Node.js | 20+ | Phase-10 Next.js UI |
| `make` (optional) | any | Convenience targets |
| `git` | any | Cloning + commit-sha provenance |

A working `python3.12` on `$PATH` and a Docker daemon are the two
hard requirements.

## Backend — first boot

```bash
git clone <repo-url> memory-cl
cd memory-cl

# 1. Python venv + editable install
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"

# 2. Backend + four storage services
cp .env.development .env
docker compose up -d        # postgres, qdrant, neo4j, redis (+ api)

# 3. Verify
curl -fsS http://localhost:8000/health/live   # → 200
curl -fsS http://localhost:8000/health/ready  # → 200 with all components ok
curl -fsS http://localhost:8000/status        # → boot stages all "ok"
```

If `/health/ready` returns 503, one of the backends isn't reachable.
Inspect:

```bash
docker compose logs postgres neo4j qdrant redis
```

## Run the test suite

```bash
.venv/bin/pytest -q          # 442 tests across Phases 1–9
.venv/bin/ruff check .       # lint clean
```

## UI — Phase 10

```bash
cd ui
npm install
# Optional: point at a non-default backend
export MEMORY_CL_BACKEND_URL=http://localhost:8000
npm run dev
# Open http://localhost:3000
```

The Next.js dev server proxies `/api/*` to the backend (configured in
`ui/next.config.mjs`), so the browser always speaks same-origin.

## Static inspector (Phase 9)

The backend serves a minimal HTML inspector at `http://localhost:8000/ui`
when `UI_ENABLED=true` (default). It's intentionally read-only and
zero-dependency — handy for environments where you can't run Node.

## CLI

```bash
.venv/bin/memcl --help
.venv/bin/memcl status
.venv/bin/memcl ingest /path/to/repo --repo-id acme --commit-sha "$(git -C /path/to/repo rev-parse HEAD)"
.venv/bin/memcl query "auth flow" --repo-id acme
```

See [19_CLI_REFERENCE](19_CLI_REFERENCE.md) for every command.

## Common setup mistakes

| Symptom | Fix |
|---|---|
| `pip install -e .` fails on `pydantic-settings` wheel | Upgrade pip ≥ 24: `.venv/bin/pip install -U pip` |
| `/health/ready` 503 with `qdrant: not connected` | Wait ~10s after `docker compose up`; Qdrant is the slowest to come ready |
| `/status` shows `boot_failed_stages: ["audit_chain"]` | Process started without `core/safety` wired — pull `main` again |
| `npm run dev` errors on `cytoscape-fcose` | `npm install` did not complete — re-run with `--no-audit` |
| `MCP_API_KEY` enforced and CLI 401s | `export MEMCL_API_KEY=<same-value-as-backend>` |
| Postgres `relation "ingestion_units" does not exist` | Lifespan didn't run `ensure_schema` — check `apps/api/lifespan.py` for boot errors |

## Verifying the install

After first boot, run the golden integration tests:

```bash
.venv/bin/pytest tests/test_golden_phase{2,3,4,5,6,7,8}.py -q
# expected: 30+ passing, ruff clean
```

If those pass, ingest the included fixture and query it through the
CLI to confirm the round-trip:

```bash
.venv/bin/memcl ingest "$PWD/tests/fixtures/sample_repo" \
    --repo-id acme --commit-sha demo
.venv/bin/memcl query "auth flow" --repo-id acme
```

You should see a `RetrieveResult` JSON with non-zero `vector_hits`
and a populated `packet.context`.

---

Next: [05 — Local Development](05_LOCAL_DEVELOPMENT.md)
