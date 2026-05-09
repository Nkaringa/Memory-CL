from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StorageHealth:
    """Result of a connectivity probe for a single backend."""

    name: str
    ok: bool
    latency_ms: float
    error: str | None = None


@runtime_checkable
class StorageClient(Protocol):
    """Lifecycle contract every storage backend implements.

    Storage clients are constructed with raw connection parameters (URLs,
    credentials). They MUST NOT import `core` — settings are passed in by
    the composition root in `apps`.
    """

    name: str

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def ping(self) -> StorageHealth: ...
