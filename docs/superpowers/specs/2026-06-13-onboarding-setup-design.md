# Phase 1 — Setup & Onboarding (runtime config + key management)

**Date:** 2026-06-13 · **Branch:** feat/onboarding-setup
**Decisions (user-approved):** full scope (wizard + Settings + CLI); Postgres-backed runtime config.

## Goal
Let a user generate/rotate the MCP key, set the OpenAI key, and choose the embedding mode
self-serve — via a first-run UI wizard, a Settings panel, and `memcl` CLI commands — with changes
applying WITHOUT a container restart. Foundation for lite mode + local embeddings.

## NON-NEGOTIABLE SAFETY (this touches live auth + embeddings)
- The existing env-configured VM MUST keep working untouched until merge.
- **Precedence:** Postgres runtime config OVERRIDES env when set; falls back to env when unset.
- **Seed-on-first-boot:** if the `app_config` row is empty AND env has MCP_API_KEY / OPENAI_API_KEY,
  seed Postgres from env on startup → existing VM's keys carry over seamlessly, Postgres becomes
  source of truth, nothing breaks.
- Full test on the branch + VM:3001 (separate port) before merge. Never destabilize live :3000.

## Architecture

### Runtime config layer (backend)
- New table `app_config` (single logical row): `mcp_api_key`, `openai_api_key`, `embedding_mode`
  ('openai' | 'local'), `embedding_model`, `onboarding_completed` (bool), `updated_at`.
- `RuntimeConfig` service: reads `app_config` (cached, invalidated on write); exposes
  `mcp_api_key()`, `openai_api_key()`, `embeddings_enabled()`, `embedding_mode()`. Each resolves
  runtime-value-if-set ELSE Settings/env value.
- Wire into the existing read points:
  - `ApiKeyDep` / auth → check `RuntimeConfig.mcp_api_key()` (not just `settings.mcp_api_key`).
  - lifespan + ingest router embedder construction → use `RuntimeConfig` (key + mode).
  - `/status` `embeddings_enabled` / `mcp_tool_count` unaffected; add config fields.

### Bootstrap auth rule (chicken-and-egg)
- When NO mcp key is configured (fresh install): the setup endpoints are OPEN (the API is already
  open in keyless dev mode) so the wizard can generate the first key.
- Once a key IS configured: setup/rotate endpoints REQUIRE the current key (ApiKeyDep). So nobody
  can rotate a configured system's key anonymously.

### Endpoints (`apps/api/routers/config.py`, new)
- `GET /config` — onboarding state: `{configured: bool, onboarding_completed, embedding_mode,
  embeddings_enabled, has_openai_key: bool, mcp_key_hint: "••••abcd" | null}`. NEVER returns raw keys.
  Unauthenticated (the wizard needs it pre-key).
- `POST /config/mcp-key/generate` — generate a secure random key (secrets.token_urlsafe), store,
  return it ONCE in the response (only chance to copy). Open if unconfigured, else require key.
- `POST /config/mcp-key/rotate` — same, requires current key. Warns: agents must re-add.
- `POST /config/openai-key` `{api_key | null}` — set/clear. Validates format. Require key (or open if
  unconfigured). Triggers embedder rebuild on next ingest (or hot-swap).
- `POST /config/embedding-mode` `{mode}` — 'local' | 'openai'. (local depends on Phase 2 local
  embedder; for Phase 1, accept the setting + note local needs Phase 2; default openai.)
- `POST /config/complete-onboarding` — marks onboarding_completed=true.

### UI
- **First-run wizard** (`/setup` route, or auto-redirect when `GET /config` says !onboarding_completed
  AND !configured): Step 1 Generate access key (one click → key shown + copy, "save this — it won't be
  shown again") · Step 2 Embeddings (Local [Phase 2 — note "coming"] / OpenAI [paste key]) · Step 3
  Connect your agent (the `claude mcp add …` command pre-filled with the new key + copy) · Step 4 Add
  first repo (link to Repositories). On finish → complete-onboarding → Command Center.
- **Settings → Access & Keys panel** (upgrade the read-only Settings page): show masked MCP key +
  Regenerate (confirm modal, shows new key + fresh connect command); OpenAI key set/replace/clear +
  status; embedding mode toggle (openai now, local greyed "Phase 2"); all server-side (browser never
  sees raw keys except the one-time reveal on generate).
- UI middleware: continue env-injection as fallback; additionally, the UI's server-side fetches the
  current key from the api when needed (so a rotated key works without redeploy). Keep simple: the
  key-bearing calls already go through the proxy; the api validates against RuntimeConfig.

### CLI (`apps/cli`)
- `memcl setup` — interactive first-run: hits `/config`; if unconfigured, generates key (prints +
  saves to ~/.memcl/config), prompts OpenAI key (or "skip — local later"), then prints the pre-filled
  `claude mcp add` command + "memcl ingest <path>" next step.
- `memcl key generate|show|rotate` — manage the MCP key via the endpoints.
- `memcl config set openai-key <k>` — set OpenAI key.
- These reuse the SDK; add SDK methods for the config endpoints.

## Testing
- Backend: unit tests for RuntimeConfig precedence/seed; config endpoints (bootstrap-open-then-locked,
  generate returns once, masked GET, rotate requires key); auth reads RuntimeConfig; embedder uses it.
- Integration (golden stack): app_config table created; seed-from-env; key set → auth enforces new key.
- UI: tsc + build; Playwright drive the wizard (generate → copy → connect → finish) and Settings rotate.
- VM:3001 branch deploy → verify the seed-from-env carried the live key (no lockout), generate/rotate
  works, embeddings still on. ONLY merge after this passes.

## Out of scope (later phases)
- Local embedder impl (Phase 2 — this phase just exposes the mode setting).
- Per-user keys / RBAC (team phase).
- Lite-mode auto-gen on first run (Phase 5 — but the config layer is built to support it).
