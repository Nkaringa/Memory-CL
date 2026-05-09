# 20 · SDK Guide

← back to [index](00_INDEX.md) · related: [07_API_REFERENCE](07_API_REFERENCE.md), [19_CLI_REFERENCE](19_CLI_REFERENCE.md), [18_UI_GUIDE](18_UI_GUIDE.md)

Two SDKs ship in this repo:

- **Python SDK** — `sdk/` package · `AsyncMemoryClient` (httpx)
- **TypeScript SDK** — `ui/lib/api.ts` · `AsyncMemoryClient` (fetch)

Both wrap the same nine HTTP endpoints. Both are typed end-to-end.
Both raise `MemoryClientError` (Python) / throw `MemoryClientError`
(TS) on non-2xx HTTP.

## Python SDK

### Install

The SDK ships in the same package as the backend:

```bash
pip install -e ".[dev]"
```

Or vendor `sdk/` into a downstream service.

### Basic usage

```python
import asyncio
from sdk import AsyncMemoryClient

async def main():
    async with AsyncMemoryClient(
        base_url="http://localhost:8000",
        api_key="<MCP_API_KEY-if-set>",
    ) as client:
        status = await client.get_status()
        print(status.environment, status.mcp_tool_count)

        ingested = await client.ingest_repository(
            repo_id="acme",
            repo_path="/abs/path/to/repo",
            commit_sha="deadbeef",
        )
        print(ingested.metrics)

        retrieved = await client.retrieve(
            text="auth flow", repo_id="acme", top_k=5,
        )
        for entry in retrieved.packet["context"]:
            print(entry["score"], entry["data"]["qualified_name"])

asyncio.run(main())
```

### Method reference

| Method | Wraps | Returns |
|---|---|---|
| `get_status()` | `GET /status` | `StatusResult` |
| `ingest_repository(repo_id, repo_path, commit_sha)` | `POST /ingest` | `IngestResult` |
| `retrieve(text, repo_id, top_k=10, ...)` | `POST /retrieve` | `RetrieveResult` |
| `list_mcp_tools()` | `GET /mcp/tools` | `list[str]` |
| `run_mcp_tool(tool, payload)` | `POST /mcp/tools/{tool}` | `McpToolResult` |
| `get_snapshot(tenant_id, state_version_token="v0")` | `POST /snapshot/build` | `SnapshotResult` |
| `replay_snapshot(snapshot_id, payload, expected_output=None)` | `POST /snapshot/replay` | `ReplayResult` |
| `get_audit_tail(limit=50)` | `GET /audit/tail` | dict |
| `verify_audit_chain()` | `GET /audit/verify` | dict |

All result types live in `sdk.types`.

### Errors

```python
from sdk import MemoryClientError

try:
    await client.ingest_repository(repo_id="r", repo_path="/nope", commit_sha="c")
except MemoryClientError as exc:
    print(exc.status_code)  # 400
    print(exc.body)         # {"detail": "repo_path is not a directory: /nope"}
    print(exc.url)          # http://localhost:8000/ingest
```

The SDK never silently swallows errors. Every non-2xx HTTP status →
`MemoryClientError`.

## TypeScript SDK

### Install

The TS SDK lives inline in `ui/lib/api.ts`. Import via the
`@/lib/api` alias from inside the `ui/` Next.js project.

```typescript
import { getMemoryClient, MemoryClientError } from "@/lib/api";

const client = getMemoryClient();
const status = await client.status();
console.log(status.environment, status.mcp_tool_count);

const result = await client.retrieve({
  text: "auth flow",
  repo_id: "acme",
  top_k: 5,
});

result.packet.context.forEach((entry) => {
  console.log(entry.score, entry.data.qualified_name);
});
```

### Constructor

```typescript
new AsyncMemoryClient({
  baseUrl?: string,        // default: "/api" (Next.js rewrite)
  apiKey?: string,
  timeoutMs?: number,      // default: 30000
  fetchImpl?: typeof fetch,
});
```

### Method reference

| Method | Wraps | Returns |
|---|---|---|
| `status()` | `GET /status` | `StatusResponse` |
| `health()` | `GET /health/ready` | `ReadinessResponse` |
| `retrieve(req)` | `POST /retrieve` | `RetrieveResponse` |
| `ingest(req)` | `POST /ingest` | `IngestResponse` |
| `listTools()` | `GET /mcp/tools` | `McpToolList` |
| `runTool(name, payload)` | `POST /mcp/tools/{name}` | `McpToolResponse` |
| `buildSnapshot({tenant_id, state_version_token?})` | `POST /snapshot/build` | `SnapshotResponse` |
| `replay({snapshot_id, payload, expected_output?})` | `POST /snapshot/replay` | `ReplayResponse` |
| `auditTail(limit?)` | `GET /audit/tail` | `AuditTailResponse` |
| `auditVerify()` | `GET /audit/verify` | `AuditVerifyResponse` |

All response types live in `ui/lib/types.ts`.

### Errors

```typescript
try {
  await client.ingest({ repo_id: "r", repo_path: "/nope", commit_sha: "c" });
} catch (err) {
  if (err instanceof MemoryClientError) {
    console.error(err.status, err.url, err.body);
  }
}
```

## Architecture rules (both SDKs)

1. **Single entry point.** Every backend call goes through the
   `AsyncMemoryClient`. No `fetch()` / `httpx.AsyncClient()` calls
   anywhere else in the codebase.
2. **Typed end-to-end.** Result types mirror the backend Pydantic
   shapes 1:1. Field names match for trivial debugging.
3. **No business logic.** SDK methods serialize a request, fire it,
   parse the response. Caching, retry, batching are explicit
   concerns of the caller (or of Phase-7 backend infrastructure).

## Adding a method

1. Add the response type to `sdk/types.py` (Python) or
   `ui/lib/types.ts` (TS).
2. Add the method to `sdk/client.py::AsyncMemoryClient` or
   `ui/lib/api.ts::AsyncMemoryClient`.
3. Add a test pattern matching `tests/test_phase9_sdk_cli.py`.

Don't add new logic to the SDK — wrap an HTTP endpoint, return the
typed result.

## Composition examples

### Python — ingest every repo + verify audit chain

```python
import asyncio
from pathlib import Path
from sdk import AsyncMemoryClient

async def ingest_all(repos: list[Path]) -> None:
    async with AsyncMemoryClient(base_url="http://localhost:8000") as c:
        for repo in repos:
            await c.ingest_repository(
                repo_id=repo.name, repo_path=str(repo), commit_sha="batch",
            )
        chain = await c.verify_audit_chain()
        assert chain["intact"], chain

asyncio.run(ingest_all([Path("/var/repos") / d for d in ("a", "b", "c")]))
```

### TypeScript — react-query hook

```typescript
import { useQuery } from "@tanstack/react-query";
import { getMemoryClient } from "@/lib/api";

export function useStatus() {
  return useQuery({
    queryKey: ["status"],
    queryFn: () => getMemoryClient().status(),
    refetchInterval: 15_000,
  });
}
```

---

Next: [21 — Deployment](21_DEPLOYMENT.md)
