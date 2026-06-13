# 19 · CLI Reference

← back to [index](00_INDEX.md) · related: [20_SDK_GUIDE](20_SDK_GUIDE.md), [07_API_REFERENCE](07_API_REFERENCE.md), [08_MCP_TOOLING](08_MCP_TOOLING.md)

`memcl` is a human-first CLI over `AsyncMemoryClient` and the v2 MCP
tool surface. Output is rich tables/colors/spinners by default
(auto-degrading on `NO_COLOR` and non-TTY); every command takes
`--json` for scripts, which emits **canonical JSON** (sorted keys,
compact separators — the v1 contract).

## Install

`memcl` ships as a `[project.scripts]` console-script. After
`pip install -e .`:

```bash
memcl --help
memcl doctor        # first stop when anything misbehaves
```

## Configuration

Precedence (highest wins): **flags > env > config file > defaults**.

| Flag | Env | Config key | Default |
|---|---|---|---|
| `--base-url` | `MEMCL_BASE_URL` | `base_url` | `http://localhost:8000` |
| `--api-key` | `MEMCL_API_KEY` | `api_key` | none |
| `--timeout` | `MEMCL_TIMEOUT` | `timeout` | `30` (`3600` for `ingest`) |
| `--request-id` | — | — | fresh uuid4 per request |

The config file lives at `~/.memcl/config.toml` (override the path with
`MEMCL_CONFIG`; written `0600` because it can hold the API key).

```bash
memcl config init     # prompts on a TTY, writes flag/env values otherwise
memcl config show     # effective values + which layer supplied each
```

## Exit codes

`0` success · `1` expected failure (HTTP error, unreachable server,
not-found) · `2` usage error. Expected failures never print tracebacks.

## Commands

### `memcl ingest [path=.]`

Ingest a repository. **Everything is inferred**: repo-id from the
directory basename, commit-sha from `git rev-parse HEAD` (falling back
to `"manual"`). Flags `--repo-id`, `--commit-sha`, `--server-path`
override.

```bash
cd ~/code/acme && memcl ingest            # repo-id "acme", HEAD sha
memcl ingest ~/code/acme --repo-id acme2  # explicit id
memcl ingest /repos/acme                  # already a server path — sent as-is
```

**Server-path model:** the API walks paths inside *its* container
(`/repos/<name>`), not your machine. Local paths are mapped to
`/repos/<basename>` and memcl prints the exact `rsync` one-liner to put
the code there first. `--server-path /repos/X` picks a different
container path.

While the request runs (minutes on big repos) memcl shows an
elapsed-time spinner and polls `/repos` so you see the unit count grow.
If the client times out, the server keeps ingesting — watch with
`memcl repos`.

### `memcl repos`

Table of every ingested repo: repo, units, files, languages.

### `memcl search "question" [-r repo] [-k 8]`

Semantic search via the `search_code` tool. Results are ranked with a
score-colored header, qualified name, `file:line`, retrieval channels,
and a 3-line snippet preview. Omit `-r` to search every repo.

```bash
memcl search "where are JA4 fingerprints parsed?" -r ja4m
memcl search "auth flow" -k 5 --json | jq '.results[0].file_path'
```

### `memcl read <qname-or-path-or-unit-id> [-r repo]`

Read one unit via `read_unit`: a `#`-comment header (location, kind,
language, signature, enclosing scope) then the content — syntax
highlighted on a TTY, raw bytes when piped, so `| less` and `| grep`
behave.

```bash
memcl read core.retrieval.hybrid_retriever.HybridRetriever -r memory-cl
memcl read pkg/auth.py          # file paths resolve to the module unit
```

Misses print the closest qualified names and exit `1`.

### `memcl explore <qname> [-r repo] [--direction all] [--depth 1]`

Graph neighborhood via `explore`. Directions: `callers`, `callees`,
`imports`, `imported_by`, `inherits`, `all`. Renders the seed header
plus a relation / distance / kind / qname / location / signature table.
`-r` may be omitted when exactly one repo is ingested.

### `memcl symbols <query> [-r repo] [--limit 20]`

Substring qualified-name lookup via `find_symbol`, rendered as a table.

### `memcl overview <repo>`

Repo orientation via `repo_overview`: language bars, unit-kind counts,
top-level module tree, most-connected units, doc files.

### `memcl status`

Humanized `/status`: boot stages with ✓/⚠/✗, safe mode, embeddings
on/off, MCP tool count. `memcl status --json` keeps the byte-identical
canonical JSON of v1.

### `memcl doctor`

Ordered diagnosis, each line ✓/✗ with the fix command:

1. config found (file / env / flags)?
2. server reachable?
3. API key accepted (cheap authed `list_repos` call)?
4. embeddings enabled?
5. any repos ingested?

Exit `0` only when everything passes.

### `memcl reembed [repo]`

Backfill real vectors. Positional repo (inferred when only one repo is
ingested); `--repo-id` still accepted.

### `memcl snapshot build --tenant-id <id> [--state-version v0]`
### `memcl snapshot replay <snapshot_id> --payload <json> [--expected <json>]`

Snapshot capture / replay verification, humanized (`✓ replay matched`).
Replay mismatches exit `1`.

## Deprecated spellings (still work)

| v1 | v2 | Notes |
|---|---|---|
| `memcl query "<text>" --repo-id X` | `memcl search` | one-line notice on stderr; `--seed-unit-ids`/`--unit-kinds` ignored |
| `memcl graph <node> --repo-id X` | `memcl explore` | one-line notice on stderr |
| `memcl snapshot --tenant-id X` | `memcl snapshot build` | notice on stderr |
| `memcl replay <id> --payload …` | `memcl snapshot replay` | notice on stderr |
| `memcl ingest /repos/X --repo-id X --commit-sha Y` | `memcl ingest` | parses unchanged |
| `memcl reembed --repo-id X` | `memcl reembed X` | parses unchanged |

## --json output rules

- Sorted keys, compact separators — two runs on the same state produce
  byte-identical stdout (pinned by `test_cli_status_json_is_canonical`).
- REST-backed commands emit the typed response model; MCP-backed
  commands emit the tool's raw `data` payload.
- Errors go to stderr as structured JSON and exit `1`:
  ```json
  {"error":"http","status_code":500,"url":"/ingest","body":{...}}
  ```
  (`"error"` is `connect` / `timeout` / `cli` for non-HTTP failures.)

## Composition examples

```bash
# Ingest every repo under a directory (ids + shas inferred)
for d in ~/code/*; do memcl ingest "$d"; done

# Watch a long-running server-side ingest
watch -n 5 'memcl repos --json | jq ".repos[] | {repo_id, units}"'

# Pipe a unit into your pager
memcl read core.mcp.tools.search_tool -r memory-cl | less

# Script over search results
memcl search "hash chain verification" --json \
    | jq -r '.results[] | "\(.score)\t\(.file_path):\(.lines)"'
```

---

Next: [20 — SDK Guide](20_SDK_GUIDE.md)
