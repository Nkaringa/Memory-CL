# Native MCP Server

← back to [index](00_INDEX.md) · related: [08_MCP_TOOLING](08_MCP_TOOLING.md), [MCP_BRIDGE](MCP_BRIDGE.md)

Memory-CL exposes a real Model Context Protocol server so MCP-aware
clients (Claude Desktop, Claude Code, Cursor, Zed, etc.) can talk to
it directly over the network — no local bridge required when the
client supports HTTP/SSE transport.

> The original REST surface (`GET /mcp/tools` + `POST /mcp/tools/{name}`)
> is **unchanged** and continues to serve. The native server is
> additive — pick whichever transport your client uses.

---

## Architecture

```
                stdio
  ┌────────────┐ ── ▶ ┌──────────────┐
  │ MCP client │       │  bridge      │ ── HTTP ─▶ Memory-CL REST
  └────────────┘       └──────────────┘
                                              ─▶ /mcp/tools/*

  ┌────────────┐ ── HTTP/SSE ──▶ Memory-CL native MCP server
  │ MCP client │                ─▶ /mcp/sse  (SSE transport)
  └────────────┘                ─▶ /mcp/http (streamable HTTP)
```

The native server reuses **the exact same** `ToolRegistry` and
`ToolExecutor` as the REST surface (see
[`apps/mcp/native_server.py`](../apps/mcp/native_server.py)). No tool
logic is duplicated; both transports route into the same executor,
so latency, audit events, OTEL spans, and error envelopes match.

---

## Endpoints

| Path | Transport | Use when… |
|---|---|---|
| `GET /mcp/tools` | REST (legacy) | You want a static tool list without an MCP session |
| `POST /mcp/tools/{name}` | REST (legacy) | You want a one-shot HTTP call returning the canonical envelope |
| `GET /mcp/sse` + `POST /mcp/sse/messages/` | MCP-protocol over SSE | Older MCP clients; long-lived sessions over an event stream |
| `* /mcp/http` | MCP-protocol over streamable HTTP | Current MCP spec; preferred for new clients |

Pick **streamable HTTP** if your client supports it. Fall back to
**SSE** otherwise.

---

## Authentication

Identical to the REST surface:

- `MCP_API_KEY` unset → dev mode, no auth
- Set in production → every request must carry one of:
  - `X-API-Key: <key>`
  - `Authorization: Bearer <key>`

Auth is enforced at the ASGI mount boundary by
[`apps/mcp/native_auth.py`](../apps/mcp/native_auth.py). Failed
auth returns `401` with `WWW-Authenticate: Bearer`.

---

## Tools exposed

Every tool registered by `apps.mcp.build_default_registry()`:

- `get_context` — full hybrid retrieval → `ContextPacket`
- `get_module_summary` — DenseModule for a module qname
- `get_related_components` — graph-derived neighborhood
- `get_risks` — risk-tagged context entries
- `query_graph` — bounded BFS over the project graph
- `ingest_repository` — kick off an ingestion pipeline run
- `update_memory` — append a memory note

The `inputSchema` for each tool is generated from its Pydantic
request model, so MCP clients that consume schemas (most of them)
get the same validation contract the REST surface enforces.

---

## Client configuration

### Claude Desktop (HTTP/SSE)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or the equivalent on your platform:

```json
{
  "mcpServers": {
    "memory-cl": {
      "transport": {
        "type": "sse",
        "url": "https://your-memcl-host:8000/mcp/sse",
        "headers": {
          "X-API-Key": "<your MCP_API_KEY>"
        }
      }
    }
  }
}
```

> If your Claude Desktop build supports streamable HTTP, prefer
> `"type": "streamable-http"` and point at `/mcp/http`.

### Claude Code (CLI)

```bash
claude mcp add memory-cl --transport sse \
  --url https://your-memcl-host:8000/mcp/sse \
  --header "X-API-Key: <your MCP_API_KEY>"
```

### Cursor

In `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "memory-cl": {
      "url": "https://your-memcl-host:8000/mcp/sse",
      "headers": { "X-API-Key": "<your MCP_API_KEY>" }
    }
  }
}
```

If your client doesn't support remote MCP transports yet, use the
**local stdio bridge** instead — see [MCP_BRIDGE](MCP_BRIDGE.md).

---

## Local usage

```bash
# 1. Bring the API up (dev or prod)
docker compose up -d

# 2. From a Python REPL, drive the native server with the SDK client
python - <<'PY'
import asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client

async def main():
    async with sse_client("http://localhost:8000/mcp/sse") as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            print([t.name for t in tools.tools])

asyncio.run(main())
PY
```

You should see all 14 tool names. Same shape on
`/mcp/http` via `mcp.client.streamable_http.streamable_http_client`.

---

## Production usage

For homelab / single-VM setups, expose port `8000` only on your LAN
or behind Tailscale (see [HOMELAB-DEPLOY](../home-install/HOMELAB-DEPLOY.md)).
Front the API with TLS termination at your reverse proxy.

A typical hardened production layout:

```
internet ─▶ caddy/nginx (TLS) ─▶ memory-cl:8000
                                  ├─ /mcp/sse   (auth: X-API-Key)
                                  ├─ /mcp/http  (auth: X-API-Key)
                                  └─ /mcp/tools (auth: X-API-Key)
```

`MCP_API_KEY` MUST be set, non-sentinel, and rotated in your secret
manager. The strict env validator
([`Settings._enforce_environment_contract`](../core/config.py))
refuses to boot in production without it.

---

## Observability

Each MCP call emits:

- A wall-clock OTEL span `mcp.native.request` with attributes:
  `tool`, `request_id`, `status`, `latency_ms`, `transport`
- An inner `mcp.tool.execution` span (from the existing executor)
- A structured log event `mcp_native_call` with the same fields

All correlated via the `request_id` allocated at the transport
boundary. See [15_OBSERVABILITY](15_OBSERVABILITY.md) for the full
span map.

---

## Determinism

The native server adds no non-determinism on top of the executor:

- Tool selection is by name (the registry is alphabetical)
- The executor's deterministic-ranking + canonical-JSON guarantees
  remain in force
- The TextContent block is `json.dumps(..., sort_keys=True, separators=(",", ":"))`

So a successful MCP call returns the same envelope (modulo the
per-call `request_id` and `latency_ms`) for the same inputs against
the same system state.

---

## Failure semantics

| Failure | Native MCP response |
|---|---|
| Unknown tool | `ToolResponse(status="failed", error_code="unknown_tool")` inside a TextContent block |
| Schema validation failure | `ToolResponse(status="failed", error_code="validation_error")` |
| Tool raised an exception | `ToolResponse(status="failed", error_code="backend_error")` |
| Auth missing/wrong | HTTP 401 at the transport boundary (no MCP envelope) |
| MCP SDK not installed at boot | The native transport silently fails to attach; REST surface keeps serving — see `native_mcp_attach_failed` in the startup log |

---

Next: [MCP_BRIDGE](MCP_BRIDGE.md) — local stdio bridge for clients
that don't yet speak HTTP/SSE MCP.
