from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from apps.api.routers import health as health_router
from apps.api.state import AppState
from core.config import Settings, get_settings
from storage.base import StorageHealth

# Force deterministic test settings — never touch a real .env from CI.
os.environ.setdefault("LOG_LEVEL", "INFO")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("OTEL_ENABLED", "false")


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_fake_client(name: str, *, ok: bool = True, error: str | None = None) -> AsyncMock:
    client = AsyncMock()
    client.name = name
    client.connect = AsyncMock(return_value=None)
    client.disconnect = AsyncMock(return_value=None)
    client.ping = AsyncMock(
        return_value=StorageHealth(name=name, ok=ok, latency_ms=1.23, error=error)
    )
    return client


def _make_repo_mocks() -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    units_repo = AsyncMock()
    graph_repo = AsyncMock()
    vector_repo = AsyncMock()
    return units_repo, graph_repo, vector_repo


@pytest.fixture
def healthy_state() -> AppState:
    units_repo, graph_repo, vector_repo = _make_repo_mocks()
    return AppState.with_default_embedder(
        postgres=_make_fake_client("postgres"),
        qdrant=_make_fake_client("qdrant"),
        neo4j=_make_fake_client("neo4j"),
        redis=_make_fake_client("redis"),
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
        embedding_dimension=32,
    )


@pytest.fixture
def degraded_state() -> AppState:
    units_repo, graph_repo, vector_repo = _make_repo_mocks()
    return AppState.with_default_embedder(
        postgres=_make_fake_client("postgres"),
        qdrant=_make_fake_client("qdrant", ok=False, error="connection refused"),
        neo4j=_make_fake_client("neo4j"),
        redis=_make_fake_client("redis"),
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
        embedding_dimension=32,
    )


@pytest.fixture
def app_factory() -> AppFactory:
    """Builds a FastAPI app whose lifespan injects a pre-built AppState.

    This bypasses the production lifespan (which would try to connect to
    real backends) and lets tests exercise routes against fakes.
    """

    def _build(state: AppState) -> FastAPI:
        @asynccontextmanager
        async def _test_lifespan(app: FastAPI) -> AsyncIterator[None]:
            app.state.app_state = state
            # Phase-10 readiness includes the MCP registry as a required
            # component. Attach the real registry so the health surface
            # has the full picture under test, just like production.
            from apps.mcp.registry import build_default_registry
            app.state.mcp_registry = build_default_registry()
            yield

        app = FastAPI(lifespan=_test_lifespan)
        app.include_router(health_router.router)
        return app

    return _build


# Type-only helper for the factory fixture above.
class AppFactory:  # pragma: no cover — pure typing alias
    def __call__(self, state: AppState) -> FastAPI: ...


@pytest.fixture
def settings() -> Settings:
    return Settings()
