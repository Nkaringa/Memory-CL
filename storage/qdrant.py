from __future__ import annotations

import time

from qdrant_client import AsyncQdrantClient

from storage.base import StorageHealth


class QdrantStorageClient:
    """Wrapper around `AsyncQdrantClient` with explicit lifecycle.

    The class is named `QdrantStorageClient` to avoid shadowing the upstream
    `qdrant_client.QdrantClient` symbol when both are imported.
    """

    name: str = "qdrant"

    def __init__(self, url: str, *, api_key: str | None = None, timeout: float = 10.0) -> None:
        self._url = url
        self._api_key = api_key
        self._timeout = timeout
        self._client: AsyncQdrantClient | None = None

    @property
    def client(self) -> AsyncQdrantClient:
        if self._client is None:
            raise RuntimeError("QdrantStorageClient not connected — call connect() first")
        return self._client

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = AsyncQdrantClient(
            url=self._url,
            api_key=self._api_key,
            timeout=self._timeout,
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def ping(self) -> StorageHealth:
        if self._client is None:
            return StorageHealth(self.name, ok=False, latency_ms=0.0, error="not connected")
        start = time.perf_counter()
        try:
            await self._client.get_collections()
            return StorageHealth(self.name, ok=True, latency_ms=(time.perf_counter() - start) * 1000)
        except Exception as exc:
            return StorageHealth(
                self.name,
                ok=False,
                latency_ms=(time.perf_counter() - start) * 1000,
                error=str(exc),
            )
