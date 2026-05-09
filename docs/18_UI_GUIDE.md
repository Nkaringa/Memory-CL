# 18 · UI Guide

← back to [index](00_INDEX.md) · related: [07_API_REFERENCE](07_API_REFERENCE.md), [20_SDK_GUIDE](20_SDK_GUIDE.md), [19_CLI_REFERENCE](19_CLI_REFERENCE.md)

Two UIs ship with the system:

1. **Phase 9 — static inspector** at `apps/ui/static/`. Zero JS deps,
   served by FastAPI at `/ui` when `UI_ENABLED=true`.
2. **Phase 10 — Next.js transparency UI** at `ui/`. Production-grade
   cognitive interface; runs on a separate process (Next dev server
   or static export).

The two are independent. Use the Phase-9 inspector when you don't
have Node available (CI, locked-down environments). Use the Phase-10
UI for everything else.

## Phase 10 — Next.js architecture

```
ui/
├── app/                      ← Next.js App Router
│   ├── layout.tsx            sidebar + dark theme + providers
│   ├── page.tsx              landing
│   ├── providers.tsx         React Query
│   ├── globals.css
│   ├── dashboard/page.tsx    /status pulse + recent audit
│   ├── retrieve/page.tsx     PRIMARY: query → ranked + Explain
│   ├── graph/page.tsx        Cytoscape BFS viewer
│   ├── ingest/page.tsx       POST /ingest + metrics
│   ├── mcp/page.tsx          tool registry + dynamic runner
│   ├── snapshot/page.tsx     build × 2 + diff + replay
│   ├── audit/page.tsx        tail + verify chain
│   └── status/page.tsx       boot stages + flags + readiness
├── components/
│   ├── QueryBox.tsx
│   ├── ResultViewer.tsx
│   ├── GraphViewer.tsx       Cytoscape + fcose
│   ├── ToolRunner.tsx
│   ├── SnapshotDiff.tsx
│   ├── AuditViewer.tsx
│   ├── StatusPanel.tsx
│   ├── ExplainPanel.tsx      per-entry "Why this result?"
│   ├── nav/
│   │   ├── Sidebar.tsx
│   │   └── CommandPalette.tsx (Ctrl+K + g-prefix shortcuts)
│   └── ui/                   minimal shadcn-style primitives
├── lib/
│   ├── api.ts                AsyncMemoryClient — SOLE SDK entry
│   ├── types.ts              TS mirrors of Pydantic shapes
│   └── utils.ts              cn, fmtMs, fmtScore, sha256Hex…
├── package.json + tsconfig + tailwind + next.config
└── README.md
```

## Architecture rule

**ALL backend access goes through `AsyncMemoryClient`.** No
component anywhere imports `fetch()` directly. Single source of
truth, single place to swap auth / retry behaviour.

```typescript
import { getMemoryClient } from "@/lib/api";

const client = getMemoryClient();
const result = await client.retrieve({ text, repo_id, top_k });
```

The `getMemoryClient()` singleton is wired to the Next.js dev
proxy (`/api/*` → backend), so the browser always speaks
same-origin and CORS never bites.

## UX principles

### 1. Explanation first

Every output surfaces:
- what happened (the data)
- why it happened (the breakdown)
- which backend modules were used (channels, pipeline trace)

Per-entry "Explain this result" panels reconstruct the Phase-4
ranking formula client-side from `FEATURE_WEIGHTS` (pinned at
0.35/0.25/0.20/0.15/0.05) so the math is verifiable without
trusting the backend.

### 2. Dual mode

Every page exposes a Simple ↔ Advanced toggle.
Advanced reveals raw JSON via `JsonView`, request_id, latency_ms,
and the full backend payload.

### 3. Determinism visibility

Every result viewer shows `request_id`, `latency_ms`, channel hits,
pipeline stages, confidence. This is the spec's "what was
retrieved, why, and how the system decided it" rendered as UI.

## Page map

| Route | Component composition |
|---|---|
| `/` | Landing (4 pillar cards) |
| `/dashboard` | `StatusPanel` + recent-audit list + pipeline summary |
| `/retrieve` | `QueryBox` → mutation → `ResultViewer` (with `ExplainPanel`) |
| `/graph` | Form → `query_graph` MCP tool → `GraphViewer` |
| `/ingest` | Form → `POST /ingest` → metrics cards + `JsonView` |
| `/mcp` | `GET /mcp/tools` → tool list → `ToolRunner` |
| `/snapshot` | Form → `POST /snapshot/build` → `SnapshotDiff` + `ReplayPanel` |
| `/audit` | `GET /audit/tail` + `GET /audit/verify` → `AuditViewer` |
| `/status` | `StatusPanel` + backend readiness from `/health/ready` |

## Keyboard

- **⌘/Ctrl + K** — command palette
- **g d** → /dashboard, **g r** → /retrieve, **g g** → /graph,
  **g i** → /ingest, **g m** → /mcp, **g s** → /snapshot,
  **g a** → /audit, **g t** → /status

## Design system

- Dark mode default (engineering surface).
- Monospace for IDs, hashes, qnames.
- Subtle borders, low-noise palette.
- Accent color (`#58a6ff`) reserved for active state + key signals.
- Status pills: ok (green) / warn (orange) / bad (red) / muted (gray).

## Run

```bash
cd ui
npm install
npm run dev    # http://localhost:3000
```

Set `MEMORY_CL_BACKEND_URL` if the backend is not at
`http://localhost:8000`.

For production:
```bash
npm run build
npm run start
```

## Phase 9 static inspector

`apps/ui/static/` ships a single-page HTML app with five tabs
(retrieval / graph / ingestion / snapshots / audit) that calls the
backend API directly. Vanilla JS, no build step. Useful when
deploying a Node runtime is impractical.

Disable it by setting `UI_ENABLED=false`.

## What this UI does NOT do

- No business logic — pages render data; intelligence belongs to the backend.
- No `fetch()` outside `lib/api.ts` — enforced by convention.
- No mutation of audit / snapshot / replay outputs — the UI shows
  what the backend returned, byte for byte.
- No write paths beyond what the backend supports — the UI is a
  transparency layer, not a CMS.

---

Next: [19 — CLI Reference](19_CLI_REFERENCE.md)
