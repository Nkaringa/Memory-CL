# memcl CLI v2 — human-first CLI over the v2 tool surface

**Date:** 2026-06-12 · **Branch:** `feat/cli-v2`

## Problem

`memcl` v1 (209 lines, 7 subcommands) dumps raw canonical JSON, demands flags it
could infer (`--repo-id` next to a path whose basename IS the repo id), shows
nothing during multi-minute ingests, and renders errors as JSON blobs or
tracebacks. The MCP tools v2 surface (search_code, read_unit, explore,
find_symbol, list_repos, repo_overview, read_file) shipped agent-first quality;
the CLI is the human's surface and must meet the same bar.

## Command tree

```
memcl
├── ingest [path=.]  [--repo-id ID] [--commit-sha SHA] [--server-path /repos/X]
├── repos                                   # table of ingested repos
├── search "question" [-r REPO] [-k 8]      # search_code, ranked render
├── read <qname|path|unit_id> [-r REPO]     # read_unit, pager-friendly
├── explore <qname> [-r REPO] [--direction all] [--depth 1]
├── symbols <query> [-r REPO] [--limit 20]  # find_symbol
├── overview <repo>                         # repo_overview, bars + tree
├── status                                  # humanized /status
├── doctor                                  # config → reach → auth → embeddings → repos
├── reembed [repo]
├── snapshot {build,replay}
├── config {init,show}
└── (deprecated, kept working) query → search · graph → explore · replay →
    snapshot replay · snapshot --tenant-id X → snapshot build
```

## Output philosophy

1. **Human output is the default.** Tables, score colors, spinners via `rich`
   (pure-python, pinned). rich auto-degrades on NO_COLOR / non-TTY — no flags
   needed.
2. **`--json` on every command** emits the exact raw API payload (for REST
   endpoints: the typed model dump, byte-stable canonical JSON as in v1; for
   MCP-backed commands: the tool's raw `data` dict). Pipelines opt in with one
   flag; `--json` errors keep v1's structured-JSON-on-stderr contract.
3. **Errors teach, never traceback.** Connection refused → "Can't reach
   `<url>` — is the server up? Try: memcl doctor". 401 → "API key rejected —
   set MEMCL_API_KEY or run memcl config init". Tool-level `found=false`
   responses render their `hint`/`suggestions`.
4. **Exit codes:** 0 success, 1 expected failure (HTTP error, not-found,
   unreachable), 2 usage (argparse). `search` with zero hits exits 0 (the
   query succeeded); `read`/`explore` misses exit 1.
5. **Raw content stays raw.** `memcl read` prints a `#`-prefixed header then
   the unit content verbatim to stdout (no boxes) so `| less` / `| grep` work.

## Inference

- `ingest` repo-id ← directory basename; commit-sha ← `git -C <path>
  rev-parse HEAD`, falling back to `"manual"`.
- **Server-path model:** the API walks paths inside ITS container
  (`/repos/<name>`). A path already under `/repos/` (or `--server-path`) is
  sent as-is. A local-looking path is mapped to `/repos/<basename>` and the
  CLI prints the server-path explanation + the exact rsync one-liner (host
  derived from base_url) before sending.
- `explore`/`overview`/`reembed` with no repo: if exactly one repo is
  ingested, use it; otherwise list the candidates and exit 1.
- `ingest` default timeout is raised to 3600 s unless the user set one —
  ingests legitimately take minutes.

## Ingest progress

The POST /ingest request runs as an asyncio task; while it's pending the CLI
shows an elapsed-time spinner and polls GET /repos every ~2 s, surfacing the
repo's growing unit count as the progress signal. On client timeout: "server
is still ingesting — run `memcl repos` to watch progress" (exit 1, no trace).

## Config precedence

`flags > env (MEMCL_BASE_URL / MEMCL_API_KEY / MEMCL_TIMEOUT) >
~/.memcl/config.toml (override path: MEMCL_CONFIG) > defaults
(http://localhost:8000, no key, 30 s)`.

`config init` prompts (with sensible defaults) on a TTY, writes the file
0600; non-interactive runs write flag/env/default values directly. `config
show` prints each effective value with its source; api_key is masked.

## SDK additions (sdk/client.py + sdk/types.py)

Thin typed wrappers over `/mcp/tools/*` + `/repos`:
`search_code`, `read_unit`, `explore`, `find_symbol`, `repo_overview`
(unwrap the McpToolResult envelope; `status=failed` raises
MemoryClientError) and `get_repos` (GET /repos, unauthenticated). Models are
`extra="ignore"` mirrors of the v2 tool payloads — only fields the renderer
needs are typed.

## Layout

```
apps/cli/main.py     # parser, dispatch, command handlers (+ back-compat subparsers)
apps/cli/config.py   # config file IO + precedence resolution
apps/cli/render.py   # all rich rendering (UI class, tables, doctor, status)
```

Back-compat spellings are REAL (hidden-help) subparsers, not argv rewriting —
`query`/`graph` print a one-line deprecation notice on stderr then delegate to
search/explore handlers.

## Out of scope

read_file as a separate command (`read` accepts file paths and read_unit
resolves them to the module unit); audit subcommands; windows-specific paths.
