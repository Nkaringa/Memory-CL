"""Mount native MCP transports (SSE + streamable-HTTP) on FastAPI.

We expose two transports so different MCP clients can connect:

    GET  /mcp/sse                — SSE handshake + event stream
    POST /mcp/sse/messages        — SSE message channel (companion endpoint)
    *    /mcp/http                — Streamable-HTTP (current MCP spec)

Both transports terminate inside the same ``Server`` instance built by
``apps.mcp.native_server.build_native_mcp_server``. The existing REST
``/mcp/tools`` and ``/mcp/tools/{name}`` endpoints remain mounted on
the FastAPI app and are NOT touched by this module.

LIFECYCLE
---------
Streamable HTTP requires an async-context-managed session manager.
We hand a context manager back via ``streamable_http_lifespan(app)``;
the existing ``apps/api/lifespan.py`` enters it during startup and
leaves it on shutdown alongside the rest of the infrastructure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from starlette.routing import Mount

from apps.mcp.native_auth import McpApiKeyMiddleware
from apps.mcp.native_server import build_native_mcp_server
from core.mcp.execution import ToolExecutor, ToolRegistry

# Mount paths — DELIBERATELY disjoint from the REST surface so the
# REST routes (/mcp/tools and /mcp/tools/{name}) keep working.
MCP_SSE_PATH = "/mcp/sse"
MCP_HTTP_PATH = "/mcp/http"


def attach_native_mcp(
    app: FastAPI,
    *,
    registry: ToolRegistry,
    executor: ToolExecutor,
    get_runtime_config: Callable[[], Any] | None = None,
    get_token_cache: Callable[[], Any] | None = None,
) -> NativeMcpHandle:
    """Mount native MCP transports on the running FastAPI app.

    The returned handle exposes the streamable-HTTP session manager's
    async context — callers integrate it into their own lifespan to
    guarantee clean teardown.

    `get_runtime_config` (optional) lets the ASGI auth middleware resolve
    the MCP key from `RuntimeConfig` (Postgres-over-env) so a key
    set/rotated at runtime is enforced on the native transports too. When
    omitted, the middleware falls back to env Settings — the pre-onboarding
    behavior.
    """
    server = build_native_mcp_server(
        registry=registry,
        executor=executor,
        get_app_state=lambda: getattr(app.state, "app_state", None),
    )

    sse_app, sse_session_manager = _build_sse_transport(server)
    http_app, http_session_manager = _build_streamable_http_transport(server)

    app.router.routes.append(
        Mount(
            MCP_SSE_PATH,
            app=McpApiKeyMiddleware(
                sse_app,
                get_runtime_config=get_runtime_config,
                get_token_cache=get_token_cache,
            ),
            name="mcp-sse",
        ),
    )
    app.router.routes.append(
        Mount(
            MCP_HTTP_PATH,
            app=McpApiKeyMiddleware(
                http_app,
                get_runtime_config=get_runtime_config,
                get_token_cache=get_token_cache,
            ),
            name="mcp-http",
        ),
    )

    return NativeMcpHandle(
        server=server,
        sse_session_manager=sse_session_manager,
        http_session_manager=http_session_manager,
    )


# ---------------------------------------------------------------------------
# SDK-shape adapters
# ---------------------------------------------------------------------------
def _build_sse_transport(server: Any) -> tuple[Any, Any]:
    """Build the SSE transport ASGI app + (optional) session manager.

    The MCP SDK provides ``mcp.server.sse.SseServerTransport``. We
    wire it to a Starlette app that owns two routes: the SSE event
    stream and the message-post endpoint. The session manager is
    None for SSE (the transport object itself is the manager).
    """
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Mount as StarletteMount
    from starlette.routing import Route

    transport = SseServerTransport("/messages/")

    async def _sse_handler(request: Any) -> Any:  # type: ignore[no-untyped-def]
        async with transport.connect_sse(
            request.scope, request.receive, request._send,
        ) as (read_stream, write_stream):
            await server.run(
                read_stream, write_stream, server.create_initialization_options(),
            )

    sse_app = Starlette(
        routes=[
            Route("/", endpoint=_sse_handler),
            StarletteMount("/messages/", app=transport.handle_post_message),
        ],
    )
    return sse_app, None


def _build_streamable_http_transport(server: Any) -> tuple[Any, Any]:
    """Build the streamable-HTTP transport — the modern MCP transport.

    Uses ``StreamableHTTPSessionManager`` from the SDK. The session
    manager is async-context-managed; callers must enter/exit it via
    ``NativeMcpHandle.streamable_http_lifespan``.
    """
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.routing import Mount as StarletteMount

    session_manager = StreamableHTTPSessionManager(
        app=server,
        # Memory-CL holds tool state in Postgres/Neo4j/Qdrant. The
        # session manager doesn't need its own event store.
        event_store=None,
        # MCP supports stateful sessions, but our tools are
        # idempotent — we keep the manager stateless for simplicity.
        stateless=True,
    )

    async def _streamable_handler(scope: Any, receive: Any, send: Any) -> None:
        await session_manager.handle_request(scope, receive, send)

    http_app = Starlette(
        routes=[
            StarletteMount("/", app=_streamable_handler),
        ],
    )
    return http_app, session_manager


# ---------------------------------------------------------------------------
# Lifecycle handle
# ---------------------------------------------------------------------------
class NativeMcpHandle:
    """Lifecycle handle returned from ``attach_native_mcp``.

    The handle gives the surrounding ``lifespan`` code one async
    context manager to enter for ALL native MCP transports — the
    streamable-HTTP session manager being the main thing that needs
    setup/teardown ordering.
    """

    def __init__(self, *, server: Any, sse_session_manager: Any, http_session_manager: Any) -> None:
        self.server = server
        self.sse_session_manager = sse_session_manager
        self.http_session_manager = http_session_manager

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        """Enter all transport-level async contexts for the duration of one app lifespan.

        Idempotent — callers can wrap this around their existing
        lifespan ``yield`` boundary and the session managers will be
        entered/exited cleanly even if startup partially fails.
        """
        ctx = self.http_session_manager.run() if self.http_session_manager else None
        if ctx is None:
            yield
            return
        async with ctx:
            yield


__all__ = [
    "MCP_HTTP_PATH",
    "MCP_SSE_PATH",
    "NativeMcpHandle",
    "attach_native_mcp",
]
