# MCP Stdio Bridge

← back to [index](00_INDEX.md) · related: [MCP_SERVER](MCP_SERVER.md), [08_MCP_TOOLING](08_MCP_TOOLING.md)

A thin local stdio adapter that lets MCP clients which only speak
**stdio** (the original MCP transport) connect to a remote Memory-CL
instance over HTTP.

> If your client supports HTTP/SSE MCP transport (Claude Desktop ≥
> recent, Cursor, etc.), use the **native server** instead — see
> [MCP_SERVER](MCP_SERVER.md). The bridge exists for clients that
> haven't shipped remote-MCP support yet.

---

## What it is

```
  ┌────────────────┐ stdio  ┌──────────┐  HTTP/JSON  ┌─────────────┐
  │ Claude Desktop │ ─────▶ │  bridge  │ ──────────▶ │ Memory-CL   │
  │ Claude Code    │        │ (Python) │             │  REST API   │
  │ Cursor / Zed   │        └──────────┘             └─────────────┘
  └────────────────┘
```

- Lives in [`scripts/mcp_bridge.py`](../scripts/mcp_bridge.py)
- Stateless; no business logic
- Forwards every `tools/list` and `tools/call` to the REST MCP surface
  (`GET /mcp/tools` + `POST /mcp/tools/{name}`)
- One process per MCP client launch (the client manages lifecycle)

---

## Install

The bridge needs two Python packages on the **client machine** (the
laptop running Claude Desktop, Cursor, etc.):

```bash
python3.12 -m pip install --user mcp httpx
```

Then put the bridge somewhere stable:

```bash
# Clone or symlink Memory-CL on your client machine
git clone https://github.com/Nkaringa/Mem-CL.git ~/memory-cl

# Verify the bridge can launch (Ctrl+C to exit)
python3.12 ~/memory-cl/scripts/mcp_bridge.py
```

You'll see `bridge_start url=http://localhost:8000 auth=no` on
stderr. The bridge is now waiting for stdio MCP traffic. Quit it.

---

## Configuration

The bridge reads only environment variables:

| Var | Default | Meaning |
|---|---|---|
| `MEMORYCL_URL` | `http://localhost:8000` | Memory-CL base URL (LAN IP, Tailscale IP, public hostname…) |
| `MEMORYCL_API_KEY` | unset | Sent as `X-API-Key` header. Required when the server has `MCP_API_KEY` configured |
| `MEMORYCL_TIMEOUT` | `30` | Per-HTTP-call timeout, seconds |
| `MEMORYCL_BRIDGE_LOG_LEVEL` | `INFO` | `DEBUG` for verbose stderr logging |

---

## Client configuration

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) — for Linux it's `~/.config/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memory-cl": {
      "command": "python3.12",
      "args": ["/Users/you/memory-cl/scripts/mcp_bridge.py"],
      "env": {
        "MEMORYCL_URL": "http://192.168.1.50:8000",
        "MEMORYCL_API_KEY": "<your MCP_API_KEY>"
      }
    }
  }
}
```

Restart Claude Desktop. The Memory-CL tools should appear in the
tool picker.

### Claude Code

```bash
claude mcp add memory-cl \
  --command python3.12 \
  --args /Users/you/memory-cl/scripts/mcp_bridge.py \
  --env MEMORYCL_URL=http://192.168.1.50:8000 \
  --env MEMORYCL_API_KEY=<your MCP_API_KEY>
```

### Cursor

In `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "memory-cl": {
      "command": "python3.12",
      "args": ["/Users/you/memory-cl/scripts/mcp_bridge.py"],
      "env": {
        "MEMORYCL_URL": "http://192.168.1.50:8000",
        "MEMORYCL_API_KEY": "<your MCP_API_KEY>"
      }
    }
  }
}
```

### Zed

In `~/.config/zed/settings.json` under `context_servers`:

```json
{
  "context_servers": {
    "memory-cl": {
      "command": "python3.12",
      "args": ["/Users/you/memory-cl/scripts/mcp_bridge.py"],
      "env": {
        "MEMORYCL_URL": "http://192.168.1.50:8000",
        "MEMORYCL_API_KEY": "<your MCP_API_KEY>"
      }
    }
  }
}
```

---

## Behavior

- `tools/list` — proxies `GET /mcp/tools`. The bridge fetches the
  per-tool JSON schemas from the server's OpenAPI doc so MCP clients
  can advertise complete input shapes.
- `tools/call` — proxies `POST /mcp/tools/{name}`. The full
  `ToolResponse` envelope (success or failure) is returned in a
  single `TextContent` block as canonical JSON
  (sorted keys, no whitespace).
- Network failure on the wire → the bridge produces a synthetic
  failed envelope with `error_code=backend_error` so the client UI
  shows a clean error instead of an MCP protocol fault.
- Stderr carries operational logs; stdout is reserved for MCP
  protocol traffic.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Tools don't appear in client picker | Stderr in the client's MCP log usually has the answer. Re-launch with `MEMORYCL_BRIDGE_LOG_LEVEL=DEBUG` |
| `bridge_fatal: ImportError: No module named 'mcp'` | `pip install --user mcp httpx` on the client machine |
| `remote_unreachable url=http://...` | Verify the API is reachable: `curl $MEMORYCL_URL/health/live` |
| Calls return `error_code: unauthorized` | `MEMORYCL_API_KEY` doesn't match the server's `MCP_API_KEY`. Update env in the client's MCP config |
| `error_code: backend_error` with `HTTP 401` | Same as above — auth mismatch surfaced as 401 from REST |
| Calls succeed but show no schema in client | The bridge couldn't fetch `/openapi.json`. Tools still callable; payloads validate server-side |
| `bridge_stop` log on every command | Some clients launch + tear down the bridge per-call. Normal — not a leak |

---

## Determinism

The bridge adds NO non-determinism:

- `tools/list` results are sorted by the registry (alphabetical)
- `tools/call` envelope is forwarded verbatim from the REST surface
- Bridge-generated failure envelopes are also canonical JSON

Two identical MCP calls against the same Memory-CL state produce
identical TextContent bytes (modulo the per-call `request_id`).

---

## Why proxy REST and not the native MCP server?

Two reasons:

1. **Stability** — the REST surface is older, version-pinned, and the
   most stable contract Memory-CL ships. Pinning the bridge to it
   means upgrading the server's MCP SDK doesn't break old clients.
2. **Lightness** — the bridge needs only `mcp` (for stdio) and
   `httpx` (for forwarding), not the full transport stack of the
   native server. Easier to deploy on lightweight client machines.

If you want the bridge to instead speak native MCP to Memory-CL
end-to-end, that's a future extension — open an issue.

---

Next: [MCP_SERVER](MCP_SERVER.md) — for the native server clients
that don't need a bridge.
