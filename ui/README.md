# Memory-CL · transparency UI

Next.js 14 (App Router) + TypeScript (strict) + TailwindCSS frontend
for the Memory-CL deterministic AI memory + retrieval engine.

This UI is a **cognitive interface** over Phases 1–9 — every page
exposes the underlying modules, scores, and pipeline trace so an
engineer can reason about why the system answered what it did, not
just consume the answer.

## Architecture rule

**All API calls go through `lib/api.ts` (`AsyncMemoryClient`).** No
component anywhere imports `fetch` directly. Single source of truth
for backend access, single location to swap auth or retry behaviour.

## Setup

```bash
cd ui
npm install
# Optional: point at a non-default backend
export MEMORY_CL_BACKEND_URL=http://localhost:8000
npm run dev   # http://localhost:3000
```

The Next.js dev server proxies `/api/*` to the configured backend
(see `next.config.mjs` rewrite), so the browser always talks
same-origin and CORS never bites.

## Build

```bash
npm run build
npm run start
```

## Pages

| Route        | Purpose                                                                                                       |
|--------------|---------------------------------------------------------------------------------------------------------------|
| `/`          | Landing — links into the four primary surfaces                                                                |
| `/dashboard` | System pulse · live `/status` snapshot, recent audit, pipeline summary                                        |
| `/retrieve`  | Primary cognition surface — query, ranked results, per-entry "Explain this result", pipeline trace            |
| `/graph`     | BFS over the project graph (Cytoscape), EXTERNAL nodes dimmed                                                 |
| `/ingest`    | Trigger Phase-2 IngestionPipeline + Phase-3 compression; view chunking + embedding metrics                    |
| `/mcp`       | List + dynamically run any of the seven Phase-5 MCP tools; schema viewer + response inspector                 |
| `/snapshot`  | Build snapshots, diff component hashes, replay arbitrary payloads to verify deterministic output              |
| `/audit`     | Hash-chained audit log viewer · chain integrity verification · per-entry inspection                           |
| `/status`    | Boot stage tracker, SafeModeController state, feature flags, backend readiness                                |

Every page exposes a **Simple ↔ Advanced** toggle. Advanced reveals
raw JSON, request_id, latency_ms, and the full backend payload.

## Keyboard

- **⌘/Ctrl + K** — command palette
- **g d** → /dashboard, **g r** → /retrieve, **g g** → /graph,
  **g i** → /ingest, **g m** → /mcp, **g s** → /snapshot,
  **g a** → /audit, **g t** → /status

## File tree

```
ui/
├── app/
│   ├── layout.tsx          ← sidebar + dark theme + providers
│   ├── page.tsx            ← landing
│   ├── providers.tsx       ← React Query
│   ├── globals.css
│   ├── dashboard/page.tsx
│   ├── retrieve/page.tsx
│   ├── graph/page.tsx
│   ├── ingest/page.tsx
│   ├── mcp/page.tsx
│   ├── snapshot/page.tsx
│   ├── audit/page.tsx
│   └── status/page.tsx
├── components/
│   ├── QueryBox.tsx
│   ├── ResultViewer.tsx
│   ├── GraphViewer.tsx     ← Cytoscape + fcose
│   ├── ToolRunner.tsx
│   ├── SnapshotDiff.tsx
│   ├── AuditViewer.tsx
│   ├── StatusPanel.tsx
│   ├── ExplainPanel.tsx    ← per-entry "Why this result?"
│   ├── nav/
│   │   ├── Sidebar.tsx
│   │   └── CommandPalette.tsx
│   └── ui/                 ← minimal shadcn-style primitives
│       ├── card.tsx
│       ├── button.tsx
│       ├── input.tsx
│       ├── badge.tsx
│       ├── tabs.tsx
│       ├── switch.tsx
│       ├── scroll-area.tsx
│       └── json-view.tsx   ← read-only Monaco-equivalent
├── lib/
│   ├── api.ts              ← AsyncMemoryClient (single SDK entry)
│   ├── types.ts            ← TS mirrors of the backend Pydantic shapes
│   └── utils.ts            ← cn(), fmtMs, fmtScore, sha256Hex, …
├── styles/
├── package.json
├── tsconfig.json
├── tailwind.config.ts
├── postcss.config.mjs
└── next.config.mjs
```

## Backend contract

The UI consumes only the endpoints the Phase-9 backend documents:

```
GET  /status
GET  /health/ready
POST /retrieve
POST /ingest
POST /mcp/tools/{name}
GET  /mcp/tools
POST /snapshot/build
POST /snapshot/replay
GET  /audit/tail
GET  /audit/verify
```

If the backend gates `/mcp` behind an API key, set `MCP_API_KEY` in
the backend env and pass it from this UI by extending
`AsyncMemoryClient` with the matching header (the constructor
already accepts `apiKey`).

## What this UI does NOT do

- No business logic — pages compose components, components render
  data; intelligence belongs to the backend.
- No `fetch()` outside `lib/api.ts` — enforced by convention; a
  single grep keeps it honest.
- No mutation of audit / snapshot / replay outputs — the UI
  presents what the backend returns, byte-for-byte.

This UI is a transparency layer over a deterministic engine. Same
backend state + same query → same screen.
