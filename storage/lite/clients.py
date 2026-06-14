"""Lite storage-client wrappers — `StorageClient`-shaped façades over the
embedded backends so the lifespan + health code treat lite exactly like
server (connect/disconnect/ping/name), while exposing the one raw handle a
few call sites still need (`postgres.engine`, `qdrant.client`).
"""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.base import StorageHealth
from storage.lite.vector_repo import LiteVectorStore


class LiteSqliteClient:
    """Stands in for `PostgresClient`: exposes the shared SQLite `.engine`."""

    name: str = "postgres"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        await self._engine.dispose()

    async def ping(self) -> StorageHealth:
        start = time.perf_counter()
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return StorageHealth(
                self.name, ok=True, latency_ms=(time.perf_counter() - start) * 1000
            )
        except Exception as exc:
            return StorageHealth(
                self.name, ok=False,
                latency_ms=(time.perf_counter() - start) * 1000, error=str(exc),
            )


class LiteVectorClient:
    """Stands in for `QdrantStorageClient`: `.client` is the numpy store
    (which exposes the `search()` the retriever needs). `connect()` ensures
    the vector tables exist."""

    name: str = "qdrant"

    def __init__(self, store: LiteVectorStore) -> None:
        self._store = store

    @property
    def client(self) -> LiteVectorStore:
        return self._store

    async def connect(self) -> None:
        await self._store.ensure_schema()

    async def disconnect(self) -> None:
        return None

    async def ping(self) -> StorageHealth:
        return StorageHealth(self.name, ok=True, latency_ms=0.0)


class LiteNeo4jClient:
    """Stands in for `Neo4jClient` for the clients tuple + health. The graph
    repo (LiteGraphRepository) is wired separately over the SQLite engine, so
    no driver is needed here."""

    name: str = "neo4j"

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def ping(self) -> StorageHealth:
        return StorageHealth(self.name, ok=True, latency_ms=0.0)


__all__ = ["LiteNeo4jClient", "LiteSqliteClient", "LiteVectorClient"]
