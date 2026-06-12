# Phase-3: OpenAI embeddings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Real semantic vectors in Qdrant so the retrieve vector channel returns hits. User-approved design: OpenAI `text-embedding-3-small` (1536-dim — matches existing collections, no migration), embed `qualified_name + signature + docstring + content` per unit, missing key = today's placeholder behavior, never fail ingest on embed errors, incremental by `source_sha`, reembed backfill endpoint + CLI, UI banner becomes conditional.

**Key existing pieces (recon verified):** `core/embeddings/embedder.py` `Embedder` Protocol (`dimension`, `embed_batch`) + `DeterministicEmbedder(1536)`; `core/embeddings/embedding_pipeline.py` `EmbeddingPipeline` (chunk→embed→upsert, point_id==unit_id, first-chunk vector); `storage/qdrant_repo.py:71` sets `has_vector` from `point.vector is not None`; `core/retrieval/vector_retriever.py:99` skips `has_vector: False`; `core/config.py:32` `Settings(BaseSettings)`; ingest flow writes placeholder points in `core/ingestion/pipeline.py` `_ingest_file` (vector=None), which already fetches `existing` units for reconciliation (gives us changed-set detection for free).

**No new Python deps** — use `httpx` (already pinned) against the OpenAI REST API directly. Zero lockfile churn, zero B8-class risk.

### Task 1: `OpenAIEmbedder` + Settings

Files: `core/embeddings/openai_embedder.py` (new), `core/embeddings/__init__.py`, `core/config.py`, tests.

- `OpenAIEmbedder(api_key, model="text-embedding-3-small", dimension=1536, batch_size=100, timeout=30)` implementing the `Embedder` Protocol. `embed_batch`: split into batches, POST `https://api.openai.com/v1/embeddings` `{"model", "input": [...]}` with `Authorization: Bearer`, map `data[i].embedding` → tuples, preserve order. Retry 429/5xx with exponential backoff (3 attempts); raise a typed `EmbeddingProviderError` on final failure. Truncate each input to ~8000 tokens-worth of chars (32k chars) defensively.
- Settings: `openai_api_key: str | None = None`, `embedding_model: str = "text-embedding-3-small"`, computed property/helper `embeddings_enabled` (key present).
- Tests: mocked httpx (respx or monkeypatched AsyncClient — follow existing httpx test idiom if any): batching boundaries, order preservation, retry-then-success, final-failure raises, truncation.

### Task 2: ingest wiring + reembed backfill

Files: `core/ingestion/pipeline.py`, `core/ingestion/context.py` (if embedder needs to ride ctx), `apps/api/routers/ingest.py` (construct embedder from Settings + reembed route), `apps/cli/main.py` (reembed cmd), `storage/postgres_repo.py` + protocol IF a list-units-for-repo method is missing (check first), tests.

- `IngestionPipeline` accepts optional `embedding_pipeline`. In `_ingest_file`, compute `changed_units` = units whose unit_id is new OR whose source_sha differs from the `existing` rows already fetched; after the placeholder vector write, if embedding pipeline present and changed_units non-empty: run it for those units inside try/except — on failure emit warning event (`embed_failed`, degraded) and continue; placeholders stay. Metrics: `units_embedded` counter.
- Ingest router: build `OpenAIEmbedder` + `EmbeddingPipeline` (reuse existing `ChunkingStrategy` default) only when settings key present.
- `POST /ingest/reembed` `{repo_id}` (auth-free like /ingest, mirrors its idiom): stream all units for repo from Postgres (add `list_units_for_repo(repo_id)` if absent — B16/CAST lessons apply), embed in batches, upsert vectors; response `{repo_id, units_embedded, failed}`; 409/400 when embeddings disabled. CLI `memcl reembed --repo-id X` mirroring existing command idiom.
- Tests: pipeline embeds only changed units (fake embedder records calls); embed failure doesn't fail ingest; reembed route happy path + disabled path; CLI smoke.

### Task 3: status flag + conditional UI banner + env plumbing

Files: `apps/api/routers/status.py` (add `embeddings_enabled` to payload), `ui/lib/types.ts`, `ui/app/retrieve/page.tsx` (banner only when `!embeddings_enabled`), `ui/components/ExplainPanel.tsx` (wording conditional or neutral), `.env.production` template + `docker-compose.production.yml` (pass `OPENAI_API_KEY: ${OPENAI_API_KEY:-}` optional — MUST default-empty, not `:?required`), docs touch-up (README capability line, docs/12 if it claims pending).

- Tests: status payload includes flag both ways. UI: `npm run build`.

### Task 4: verification + ship

- `.venv/bin/pytest tests/ -q`, ruff, `cd ui && npm run build`, production Docker build of api image, push branch.
- Post-merge ops note for the user (single-line commands): add key to `.env.production.local`, `up -d` (compose change → recreate), `memcl reembed --repo-id JA4M` + `NK-Base`, then a retrieve query should show `vector_hits > 0`.
