from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from storage.base import StorageHealth


class PostgresClient:
    """Async SQLAlchemy engine wrapper.

    Connections are lazy: `connect()` builds the engine and runs a probe.
    `disconnect()` disposes the pool. `ping()` is safe to call repeatedly.
    """

    name: str = "postgres"

    def __init__(self, url: str, *, echo: bool = False, pool_size: int = 5) -> None:
        self._url = url
        self._echo = echo
        self._pool_size = pool_size
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("PostgresClient not connected — call connect() first")
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            raise RuntimeError("PostgresClient not connected — call connect() first")
        return self._sessionmaker

    async def connect(self) -> None:
        if self._engine is not None:
            return
        self._engine = create_async_engine(
            self._url,
            echo=self._echo,
            pool_size=self._pool_size,
            pool_pre_ping=True,
        )
        self._sessionmaker = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None

    async def ping(self) -> StorageHealth:
        if self._engine is None:
            return StorageHealth(self.name, ok=False, latency_ms=0.0, error="not connected")
        start = time.perf_counter()
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return StorageHealth(self.name, ok=True, latency_ms=(time.perf_counter() - start) * 1000)
        except Exception as exc:
            return StorageHealth(
                self.name,
                ok=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                error=str(exc),
            )
