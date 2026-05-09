from __future__ import annotations

import time

from neo4j import AsyncDriver, AsyncGraphDatabase

from storage.base import StorageHealth


class Neo4jClient:
    """Async Neo4j driver wrapper.

    Driver is created lazily and verified via `verify_connectivity()` so a
    bad URI fails fast at startup rather than on first query.
    """

    name: str = "neo4j"

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._driver: AsyncDriver | None = None

    @property
    def driver(self) -> AsyncDriver:
        if self._driver is None:
            raise RuntimeError("Neo4jClient not connected — call connect() first")
        return self._driver

    async def connect(self) -> None:
        if self._driver is not None:
            return
        self._driver = AsyncGraphDatabase.driver(
            self._uri,
            auth=(self._user, self._password),
        )

    async def disconnect(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    async def ping(self) -> StorageHealth:
        if self._driver is None:
            return StorageHealth(self.name, ok=False, latency_ms=0.0, error="not connected")
        start = time.perf_counter()
        try:
            await self._driver.verify_connectivity()
            return StorageHealth(self.name, ok=True, latency_ms=(time.perf_counter() - start) * 1000)
        except Exception as exc:
            return StorageHealth(
                self.name,
                ok=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                error=str(exc),
            )
