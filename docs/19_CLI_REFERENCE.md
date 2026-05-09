# 19 · CLI Reference

← back to [index](00_INDEX.md) · related: [20_SDK_GUIDE](20_SDK_GUIDE.md), [07_API_REFERENCE](07_API_REFERENCE.md)

`memcl` is a thin wrapper over `AsyncMemoryClient`. Six subcommands
map 1:1 to SDK methods. Output is **canonical JSON** to stdout
(sorted keys, compact separators) so the same input + same backend
state → byte-identical stdout. Errors emit structured JSON to
stderr and exit `1`.

## Install

`memcl` ships as a `[project.scripts]` console-script. After
`pip install -e .`:

```bash
memcl --help
```

## Global flags

| Flag | Env | Default | Purpose |
|---|---|---|---|
| `--base-url` | `MEMCL_BASE_URL` | `http://localhost:8000` | Backend URL |
| `--api-key` | `MEMCL_API_KEY` | none | MCP API key for `/mcp/*` |
| `--timeout` | `MEMCL_TIMEOUT` | `30` | Request timeout (seconds) |

## Subcommands

### `memcl ingest <repo> --repo-id <id> [--commit-sha <sha>]`

Trigger Phase-2 ingestion.

```bash
memcl ingest /var/repos/acme \
    --repo-id acme \
    --commit-sha "$(git -C /var/repos/acme rev-parse HEAD)"
```

Output: `IngestResponse` JSON.

### `memcl query "<text>" --repo-id <id> [--top-k <N>] [--seed-unit-ids ...] [--unit-kinds ...]`

Run hybrid retrieval.

```bash
memcl query "auth flow" --repo-id acme --top-k 5
memcl query "session middleware" --repo-id acme --unit-kinds fn cls
memcl query "x" --repo-id acme \
    --seed-unit-ids 8a3ad47d... ad12cb...
```

Output: `RetrieveResponse` JSON.

### `memcl graph <node> --repo-id <id> [--depth <N>]`

Bounded graph BFS via the `query_graph` MCP tool.

```bash
memcl graph pkg.utils.add --repo-id acme --depth 2
```

Accepts either a `qualified_name` or a `unit_id` (64-char hex).

Output: `QueryGraphResult` JSON.

### `memcl snapshot --tenant-id <id> [--state-version <token>]`

Build a snapshot of the current process-local view.

```bash
memcl snapshot --tenant-id acme-corp
memcl snapshot --tenant-id acme-corp --state-version v3
```

Output: `SnapshotResponse` JSON. Re-running with the same tenant +
state-token produces a snapshot whose `components` are byte-identical
(only `captured_at` differs).

### `memcl replay <snapshot_id> --payload <json> [--expected <json>]`

Verify a payload against a snapshot via JSON-hash equality.

```bash
memcl replay $(memcl snapshot --tenant-id acme-corp | jq -r .snapshot_id) \
    --payload '{"a": 1, "b": 2}' \
    --expected '{"b": 2, "a": 1}'
```

Output: `ReplayResponse` with `matches: true` when the hashes align.

### `memcl status`

Print full system posture.

```bash
memcl status
memcl status | jq '.boot_failed_stages'
```

Output: `StatusResponse` JSON.

## Output rules

- **Always JSON to stdout.** No human text. Use `jq` to drill in.
- **Canonical JSON.** Sorted keys, compact separators. Two
  successive runs on the same state produce byte-identical bytes
  — pinned by `test_cli_status_prints_canonical_json`.
- **Errors → stderr + exit 1.** Structured payload:
  ```json
  {"error": "http", "status_code": 500, "url": "/ingest", "body": {...}}
  ```

## Composition examples

### Ingest every repo under a directory

```bash
for d in /var/repos/*; do
  memcl ingest "$d" --repo-id "$(basename "$d")" \
      --commit-sha "$(git -C "$d" rev-parse HEAD)"
done
```

### Watch system status

```bash
watch -n 5 'memcl status | jq "{env: .environment, ok: .boot_overall_ok, safe: .safe_mode.enabled}"'
```

### Detect drift across deploys

```bash
# capture before
memcl snapshot --tenant-id acme-corp > /tmp/snap.before.json

# (deploy ...)

# capture after, diff
memcl snapshot --tenant-id acme-corp > /tmp/snap.after.json
diff <(jq .components /tmp/snap.before.json) \
     <(jq .components /tmp/snap.after.json)
```

### Verify the audit chain in CI

```bash
# Note: /audit/verify isn't a memcl subcommand yet — use curl + jq
curl -fsS http://localhost:8000/audit/verify \
    | jq '. | if .intact then "ok" else error("AUDIT CHAIN BROKEN") end'
```

(Adding a `memcl audit verify` subcommand is a Phase-11 task.)

---

Next: [20 — SDK Guide](20_SDK_GUIDE.md)
