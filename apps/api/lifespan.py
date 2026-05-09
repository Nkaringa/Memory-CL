from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from apps.api.state import AppState
from core import (
    configure_logging,
    get_logger,
    get_settings,
    shutdown_observability,
    start_observability,
)
from storage import (
    Neo4jClient,
    Neo4jGraphRepository,
    PostgresClient,
    PostgresIngestionRepository,
    QdrantStorageClient,
    QdrantVectorRepository,
    RedisClient,
)

_log = get_logger(__name__)


def _build_state() -> AppState:
    settings = get_settings()
    pg = PostgresClient(settings.postgres_url)
    qd = QdrantStorageClient(settings.qdrant_url)
    nj = Neo4jClient(
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password.get_secret_value(),
    )
    rd = RedisClient(settings.redis_url)
    return AppState.with_default_embedder(
        postgres=pg,
        qdrant=qd,
        neo4j=nj,
        redis=rd,
        # Repositories share the same low-level driver objects exposed by
        # the storage clients — no new connection pools are created.
        units_repo=PostgresIngestionRepository(engine_proxy(pg)),
        graph_repo=Neo4jGraphRepository(driver_proxy(nj)),
        vector_repo=QdrantVectorRepository(client_proxy(qd)),
    )


# The four `*_proxy` helpers below exist because the underlying drivers
# don't exist until `connect()` runs — we must not touch `pg.engine`
# during construction. We wrap the access in a lambda-style proxy that
# resolves at first use.
def engine_proxy(pg: PostgresClient):  # type: ignore[no-untyped-def]
    return _LazyAttr(pg, "engine")


def driver_proxy(nj: Neo4jClient):  # type: ignore[no-untyped-def]
    return _LazyAttr(nj, "driver")


def client_proxy(qd: QdrantStorageClient):  # type: ignore[no-untyped-def]
    return _LazyAttr(qd, "client")


class _LazyAttr:
    """Forward attribute access to `target.<attr>` resolved on first use.

    Lets repository constructors accept a "driver-like" object eagerly
    even though the real driver is only created during `connect()`.
    Repository code consults the driver per-call, so this is transparent.
    """

    __slots__ = ("_attr", "_target")

    def __init__(self, target: object, attr: str) -> None:
        self._target = target
        self._attr = attr

    def __getattr__(self, item: str):  # type: ignore[no-untyped-def]
        return getattr(getattr(self._target, self._attr), item)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire global infrastructure on startup; tear it down on shutdown.

    Connects all backends concurrently. Disconnect always runs, even if
    startup partially fails, to avoid leaked sockets.
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    start_observability(
        enabled=settings.otel_enabled,
        service_name=settings.otel_service_name,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint,
    )

    state = _build_state()
    app.state.app_state = state
    # Build the MCP tool registry once per process — it's stateless,
    # so cloning it across requests would just waste memory.
    from apps.mcp.registry import build_default_registry
    app.state.mcp_registry = build_default_registry()
    # Phase-9 safety controllers — exposed on app.state so routers can
    # consult / flip the safe-mode flag.
    from core.governance import AuditLogger
    from core.safety import FeatureFlagRegistry, SafeModeController
    app.state.safe_mode = SafeModeController(
        enabled=settings.safe_mode_enabled,
        reason="explicitly enabled via settings" if settings.safe_mode_enabled else "",
        triggered_by="config",
    )
    app.state.feature_flags = FeatureFlagRegistry.from_settings(settings)
    app.state.audit_logger = AuditLogger()
    _log.info("startup_begin", environment=settings.environment)

    clients = (state.postgres, state.qdrant, state.neo4j, state.redis)
    try:
        await asyncio.gather(*(c.connect() for c in clients))
        # Bootstrap durable schema/constraints exactly once per process.
        # Per-repo Qdrant collections are created lazily by the ingest
        # endpoint since they need a configured embedding dimension.
        await state.units_repo.ensure_schema()
        await state.graph_repo.ensure_constraints()

        # Phase-9 boot orchestration runs the deterministic 8-stage
        # health gate. On failure under strict_bootstrap, we flip the
        # process into safe mode rather than crashing — the operator
        # decides recovery.
        from apps.api.bootstrap import BootSequence
        outcome = await BootSequence(state=state).run()
        app.state.boot_outcome = outcome
        if outcome.safe_mode_recommended or settings.safe_mode_enabled:
            app.state.safe_mode.enable(
                reason=(
                    "explicit setting" if settings.safe_mode_enabled
                    else f"boot health: failed={outcome.failed_stages} "
                         f"degraded={outcome.degraded_stages}"
                ),
                triggered_by=(
                    "config" if settings.safe_mode_enabled else "boot_failure"
                ),
            )
        # ------------------------------------------------------------------
        # Native MCP transport attach (Phase-11).
        #
        # Done AFTER storage backends are healthy and the registry is
        # populated. Wrapped in a try/except so a missing/broken `mcp`
        # SDK degrades gracefully: REST MCP at /mcp/tools/* keeps serving
        # even when the native transports fail to come up.
        # ------------------------------------------------------------------
        native_handle = None
        try:
            from apps.mcp import attach_native_mcp
            from core.mcp.execution import ToolExecutor
            native_handle = attach_native_mcp(
                app,
                registry=app.state.mcp_registry,
                executor=ToolExecutor(app.state.mcp_registry),
            )
            app.state.native_mcp = native_handle
            _log.info(
                "native_mcp_attached",
                sse_path="/mcp/sse",
                http_path="/mcp/http",
            )
        except Exception as exc:
            _log.warning(
                "native_mcp_attach_failed",
                error=f"{type(exc).__name__}: {exc}",
                rest_mcp_still_serving=True,
            )

        _log.info(
            "startup_complete",
            backends=[c.name for c in clients],
            bootstrap=["postgres_schema", "neo4j_constraints"],
            boot_overall_ok=outcome.overall_ok,
            safe_mode=app.state.safe_mode.status.enabled,
            native_mcp=native_handle is not None,
        )
        async with AsyncExitStack() as native_stack:
            if native_handle is not None:
                await native_stack.enter_async_context(native_handle.lifespan())
            yield
    finally:
        _log.info("shutdown_begin")
        results = await asyncio.gather(
            *(c.disconnect() for c in clients), return_exceptions=True
        )
        for client, result in zip(clients, results, strict=True):
            if isinstance(result, BaseException):
                _log.warning("disconnect_error", backend=client.name, error=str(result))
        shutdown_observability()
        _log.info("shutdown_complete")
