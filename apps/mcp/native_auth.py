"""ASGI middleware enforcing MCP API-key auth on mounted transports.

The existing REST surface in ``apps/mcp/router.py`` gates each tool
call via the FastAPI ``require_mcp_api_key`` dependency. Mounted
ASGI sub-apps (the native MCP transports) DO NOT receive FastAPI
dependencies, so we re-implement the same validation rule here as a
plain ASGI middleware.

Behavior (mirrors ``apps.mcp.auth``):

    * If ``Settings.mcp_api_key`` is unset → no-op (dev mode)
    * Else accept ``X-API-Key: <key>`` OR ``Authorization: Bearer <key>``
    * Else respond 401 with ``WWW-Authenticate: Bearer``

The middleware is intentionally framework-light — it only depends on
the ASGI scope/receive/send tuple. Wrapping each mounted transport
keeps auth localized and lets us add scopes per-transport later.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from core import get_settings

# Header name we accept in priority order. Values arrive lower-cased.
_HEADER_X_API_KEY = b"x-api-key"
_HEADER_AUTHORIZATION = b"authorization"


class McpApiKeyMiddleware:
    """Wrap any ASGI app with the same key-or-Bearer rule as REST MCP."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Health/non-HTTP scopes pass through unchanged.
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        expected = settings.mcp_api_key
        if expected is None or not expected.get_secret_value():
            # Dev mode — same as the REST dependency. No auth enforced.
            await self.app(scope, receive, send)
            return

        presented = _extract_header_key(scope.get("headers", []))
        if presented is None:
            await _respond_401(send, "missing API key")
            return
        if presented != expected.get_secret_value():
            await _respond_401(send, "invalid API key")
            return

        await self.app(scope, receive, send)


def _extract_header_key(headers: list[tuple[bytes, bytes]]) -> str | None:
    """Pull X-API-Key or `Authorization: Bearer <k>` from raw ASGI headers."""
    api_key: str | None = None
    bearer: str | None = None
    for raw_name, raw_value in headers:
        # ASGI lowercases header names by spec but be defensive anyway.
        name = raw_name.lower()
        if name == _HEADER_X_API_KEY:
            api_key = raw_value.decode("latin-1").strip()
        elif name == _HEADER_AUTHORIZATION:
            value = raw_value.decode("latin-1").strip()
            if value.lower().startswith("bearer "):
                bearer = value.split(" ", 1)[1].strip()
    return api_key or bearer


async def _respond_401(send: Send, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"www-authenticate", b"Bearer"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


__all__ = ["McpApiKeyMiddleware"]
