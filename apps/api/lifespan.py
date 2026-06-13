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
from core.config import Settings
from core.config_runtime import RuntimeConfig
from core.embeddings import Embedder, OpenAIEmbedder
from storage import (
    AppConfigRepository,
    Neo4jClient,
    Neo4jGraphRepository,
    PostgresClient,
    PostgresIngestionRepository,
    QdrantStorageClient,
    QdrantVectorRepository,
    RedisClient,
)

_log = get_logger(__name__)


def _build_query_embedder(runtime: RuntimeConfig) -> Embedder | None:
    """Query-side embedder matching the document-side (ingest) embedder.

    Phase-3 ingestion embeds documents with `OpenAIEmbedder` whenever
    embeddings are enabled — query vectors must come from the SAME model
    or cosine scores against the stored vectors are noise. Returns None
    when embeddings are disabled so `AppState.with_default_embedder`
    falls back to the deterministic embedder.

    Reads key + mode from `RuntimeConfig` (Postgres-over-env) instead of
    Settings directly. `embedding_mode == 'local'` is Phase 2 — until the
    local embedder lands, 'local' resolves to no embedder (placeholder).
    """
    if not runtime.embeddings_enabled():
        return None
    if runtime.embedding_mode() == "local":
        # Phase-2 local embedder not built yet — fall back to the
        # deterministic placeholder rather than constructing OpenAI.
        _log.info("embedder_local_mode_phase2_placeholder")
        return None
    api_key = runtime.openai_api_key()
    assert api_key is not None  # embeddings_enabled() guarantees it
    return OpenAIEmbedder(
        api_key=api_key,
        model=runtime.embedding_model(),
        # 1536-dim — matches the ingest-side collections (_DEFAULT_VECTOR_SIZE).
        dimension=1536,
    )


async def _close_embedder(embedder: object) -> None:
    """Release the embedder's HTTP client at shutdown, if it has one.

    Mirrors the client-disconnect teardown: failures are logged, never
    raised — shutdown must always complete.
    """
    aclose = getattr(embedder, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception as exc:
        _log.warning("embedder_close_error", error=str(exc))


def _build_state() -> tuple[AppState, AppConfigRepository, RuntimeConfig]:
    """Build the AppState plus the runtime-config plumbing.

    The embedder is wired with the deterministic fallback here and
    upgraded to the model-backed one in `lifespan` AFTER storage connects
    and `RuntimeConfig.refresh()` has loaded the persisted keys — the
    embedder choice depends on the resolved (Postgres-over-env) key, which
    isn't readable until the engine exists.
    """
    settings = get_settings()
    pg = PostgresClient(settings.postgres_url)
    qd = QdrantStorageClient(settings.qdrant_url)
    nj = Neo4jClient(
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password.get_secret_value(),
    )
    rd = RedisClient(settings.redis_url)
    app_config_repo = AppConfigRepository(engine_proxy(pg))
    runtime = RuntimeConfig(app_config_repo, settings)
    state = AppState.with_default_embedder(
        postgres=pg,
        qdrant=qd,
        neo4j=nj,
        redis=rd,
        # Repositories share the same low-level driver objects exposed by
        # the storage clients — no new connection pools are created.
        units_repo=PostgresIngestionRepository(engine_proxy(pg)),
        graph_repo=Neo4jGraphRepository(driver_proxy(nj)),
        vector_repo=QdrantVectorRepository(client_proxy(qd)),
        embedder=None,  # deterministic fallback; upgraded post-refresh
    )
    return state, app_config_repo, runtime


async def _seed_app_config_from_env(
    repo: AppConfigRepository, settings: Settings,
) -> bool:
    """Carry the LIVE VM's env keys into `app_config` on first boot.

    NON-BREAKING / no-lockout: when the row is empty/absent AND env has
    MCP_API_KEY and/or OPENAI_API_KEY, seed Postgres from env so the
    existing deployment's keys become the runtime source of truth without
    any operator action. Idempotent: seeds ONLY when `app_config` is
    empty, so a configured row is never overwritten. Returns True if it
    wrote a seed row.
    """
    existing = await repo.get()
    if existing is not None:
        return False  # already has a config row — never overwrite

    mcp = settings.mcp_api_key
    openai = settings.openai_api_key
    mcp_val = mcp.get_secret_value() if (mcp and mcp.get_secret_value().strip()) else None
    openai_val = (
        openai.get_secret_value()
        if (openai and openai.get_secret_value().strip())
        else None
    )
    if mcp_val is None and openai_val is None:
        return False  # nothing to seed — stays fully env-driven (no row)

    await repo.upsert(mcp_api_key=mcp_val, openai_api_key=openai_val)
    _log.info(
        "app_config_seeded_from_env",
        seeded_mcp_key=mcp_val is not None,
        seeded_openai_key=openai_val is not None,
    )
    return True


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

    state, app_config_repo, runtime = _build_state()
    app.state.app_state = state
    # Runtime config (Postgres-over-env). Auth + embedder read this.
    # The snapshot is loaded below, AFTER storage connects.
    app.state.runtime_config = runtime
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
        # Runtime-config table + seed-on-first-boot + snapshot load. Done
        # before auth/embedder are consulted: the seed carries the LIVE
        # VM's env keys into app_config so the deployment keeps working
        # (no lockout, embeddings stay on), and refresh() populates the
        # snapshot the sync auth dependency reads.
        await app_config_repo.ensure_schema()
        await _seed_app_config_from_env(app_config_repo, settings)
        await runtime.refresh()
        # Upgrade the deterministic placeholder embedder to the
        # model-backed one when the RESOLVED (Postgres-over-env) config
        # enables embeddings. Built here (not in _build_state) because the
        # resolved key isn't readable until the engine + snapshot exist.
        query_embedder = _build_query_embedder(runtime)
        if query_embedder is not None:
            state.embedder = query_embedder
        _log.info(
            "runtime_config_loaded",
            configured=runtime.configured(),
            embeddings_enabled=runtime.embeddings_enabled(),
            embedding_mode=runtime.embedding_mode(),
        )

        # Phase-9 boot orchestration runs the deterministic 8-stage
        # health gate. On failure under strict_bootstrap, we flip the
        # process into safe mode rather than crashing — the operator
        # decides recovery.
        from apps.api.bootstrap import BootSequence
        # Stage 7 must verify the SAME audit chain the process appends to
        # (app.state.audit_logger), not a fresh throwaway instance.
        outcome = await BootSequence(
            state=state, audit_logger=app.state.audit_logger,
        ).run()
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
                # Native transports re-implement auth as ASGI middleware
                # (they don't get FastAPI deps). Hand it the same
                # RuntimeConfig the REST dependency uses so a rotated key
                # is enforced on /mcp/sse + /mcp/http too.
                get_runtime_config=lambda: getattr(
                    app.state, "runtime_config", None
                ),
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
        # OpenAIEmbedder holds an httpx.AsyncClient — release it like the
        # storage clients above (no-op for the deterministic fallback).
        await _close_embedder(state.embedder)
        shutdown_observability()
        _log.info("shutdown_complete")
