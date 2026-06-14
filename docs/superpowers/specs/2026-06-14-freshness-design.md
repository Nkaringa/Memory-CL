# Phase 3 — Freshness (auto-reingest on change)

**Date:** 2026-06-14 · **Branch:** feat/freshness
**Approved direction:** two repo source-models; headline = managed-git repos + polling; local-path watcher as companion.

## Goal
Keep the memory in sync with the code automatically, so what an agent retrieves always matches the current source — instead of a manual `memcl ingest` snapshot. Built as a product capability for indie / small-team / enterprise, not just the homelab.

## Core mental model
`/repos` (and managed clones) hold the **raw source code**; the **memory** (units, graph, vectors) lives in Postgres + Neo4j + Qdrant. Freshness = get fresh code to where the engine reads it, then re-ingest (which is already changed-units-only on the write side).

## Two repo source-models
1. **Local** — code already on a mounted path (today's `/repos:ro`). The user/CI/enterprise volume puts it there. Freshness = **filesystem watcher** (debounced reingest on file change). Serves: enterprise mounted volumes, CI checkouts.
2. **Managed** — a **git URL**. Memory-CL clones it into a **writable** workspace (`/managed/<repo_id>`) and keeps it pulled. Freshness = **polling** (`git fetch`, reingest when the tracked branch HEAD moves). Serves: indie + team ("point at my GitHub repo, forget it"). Webhook (instant) is a later cloud/team add-on — deferred because the homelab VM isn't internet-reachable.

`/repos` stays **read-only** (safety: engine never mutates user source). Managed clones get their **own writable named volume** at `/managed`.

## Architecture

### Repo registry (new Postgres table `repo_registry`)
Single source of truth for known repos. Columns:
`repo_id` (PK) · `source_type` ('local'|'managed') · `repo_path` (where the engine reads: `/repos/<name>` or `/managed/<repo_id>`) · `remote_url` (managed) · `branch` (managed, tracked branch) · `last_commit_sha` · `watch_enabled` (bool, default true) · `last_synced_at` · `last_change_at` · `last_error` · `created_at` · `updated_at`.
`RepoRegistryRepository` (storage/): ensure_schema, upsert_local(repo_id, repo_path, commit_sha), add_managed(...), list_all(), list_watched(), get(repo_id), set_watch_enabled, mark_synced(repo_id, sha), mark_change, mark_error, delete(repo_id).

### Shared ingest runner (extract)
`apps/api/ingest_runner.py::run_ingest(state, settings, runtime, *, repo_id, repo_path, commit_sha) -> IngestOutcome` — the in-process ingest the HTTP endpoint currently inlines (ensure_collection at active dim → make_context → `_build_embedding_components` → `IngestionPipeline.run`). Used by: the `/ingest` endpoint, the poller, the watcher. Single ingestion path. Endpoint + managed-add + poller + watcher all upsert the registry after a successful run.

### Managed lifecycle (git, in-api)
- **Add** `POST /repos/managed {remote_url, branch?, repo_id?}`: derive repo_id (from URL if absent), `git clone --branch <branch> <url> /managed/<repo_id>`, register (source_type=managed), run initial ingest, mark_synced(HEAD). Private repos: optional global `github_token` (Settings/runtime) injected into the clone URL; per-repo creds = later. CLI: `memcl repo add <url> [--branch] [--id]`.
- **Poll** (background task, every `freshness_poll_interval_seconds`, default 180): for each managed + watch_enabled repo → `git -C path fetch --quiet origin <branch>` → compare `origin/<branch>` sha vs last_commit_sha → if moved: `git -C path reset --hard origin/<branch>`, run_ingest at the new sha, mark_synced; on git/ingest failure mark_error and continue.
- **Remove** `DELETE /repos/{repo_id}/managed`: stop polling + delete the clone dir (memory rows remain; full purge is a separate concern). v1 minimal.

### Local lifecycle (watcher, in-api)
Background task (`watchfiles.awatch`, already-installed via uvicorn[standard]) over the local repo paths from the registry. Ignore `.git/`, `node_modules`, `dist`, `build`, `.venv`, `__pycache__`. Debounce a burst (`freshness_debounce_ms`, default 3000) → map changed path → repo_id (longest path-prefix) → run_ingest (re-run `git rev-parse HEAD` for commit_sha; fallback "auto"). Per-repo in-flight guard + dirty re-run if edits arrive mid-ingest. Honors watch_enabled + skips under safe_mode. `freshness_force_polling` setting for non-inotify filesystems.

### Startup reconciliation
On lifespan start (after schema): for each watched repo with a git checkout, compare current HEAD vs last_commit_sha and reingest drift — freshness holds across restarts, not only while running. Managed repos also get one poll on boot.

### Settings (core/config.py)
`freshness_enabled: bool = True` · `freshness_watch_enabled: bool = True` · `freshness_poll_interval_seconds: int = 180` · `freshness_debounce_ms: int = 3000` · `freshness_force_polling: bool = False` · `managed_repos_root: str = "/managed"` · `local_repos_root: str = "/repos"` · `github_token: SecretStr | None = None`.

### Endpoints (apps/api/routers/freshness.py + repos.py)
- `GET /freshness` — all registered repos + state (source_type, branch, sha, last_synced_at, last_change_at, last_error, watch_enabled). Unauth read (like /status) or authed — match /repos.
- `POST /repos/managed` — add managed repo (authed). Clone + initial ingest + register.
- `POST /freshness/{repo_id}/toggle {enabled}` — pause/resume (authed).
- `POST /freshness/{repo_id}/sync` — force a sync/reingest now (authed).
- `DELETE /repos/{repo_id}/managed` — deregister + delete clone (authed).

### CLI (apps/cli)
`memcl repo add <git-url> [--branch] [--id]` · `memcl freshness` (status table) · `memcl freshness sync <id>` · `memcl freshness pause|resume <id>`.

### UI (Repositories page)
Per-repo: source badge (local/managed), freshness state ("synced 2m ago" / "watching" / error), pause/resume toggle. A "+ Add managed repo" form (URL + optional branch). Minimal but functional. No new onboarding-wizard step required (adding a repo is a deliberate action, not setup friction); optionally the wizard's "first repo" step can link here.

### Deployment (Dockerfile.production + compose)
- Add `git` to the runtime image (managed clone/pull). Set `GIT_CONFIG_NOSYSTEM=1` + a writable `HOME`/git config location so git works under the read-only root.
- New **writable named volume** `managed-repos:/managed` on the api service (writable despite read-only root, like the model-cache volume). Created memcl-owned.
- Worker stays `sleep infinity` (freshness runs in-api). No new container.

## Testing
- Registry repo unit tests (upsert local, add managed, list/watched, mark_*).
- run_ingest extraction: endpoint still passes; runner callable without a Request.
- Poller logic: HEAD-moved detection, reset+reingest, error isolation — with an injected fake git runner (no real network/clone).
- Watcher logic: path→repo_id mapping, debounce coalescing, in-flight guard, watch_enabled/safe_mode skip, dirty re-run — inject the awatch change stream (no real fs events → no flakiness).
- Startup reconciliation: HEAD vs last_sha.
- Endpoint + CLI tests. Managed-add with a fake git runner.
- Golden/integration where cheap (real temp git repo + fake-embedder ingest).

## Out of scope (later)
- Git push **webhook** + tunnel (instant; cloud/team tier, needs reachable host).
- Per-file incremental ingest (pipeline rearchitecture; current reingest is already changed-units-only on writes).
- Per-repo credentials / deploy keys (v1 = optional global token); full memory purge on repo delete; multi-branch tracking.
