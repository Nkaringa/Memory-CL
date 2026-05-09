from __future__ import annotations

from storage import Neo4jClient, PostgresClient, QdrantStorageClient, RedisClient
from storage.base import StorageClient, StorageHealth


def test_clients_are_unconnected_by_construction() -> None:
    pg = PostgresClient("postgresql+asyncpg://x:y@h:5432/db")
    qd = QdrantStorageClient("http://localhost:6333")
    nj = Neo4jClient("bolt://localhost:7687", "u", "p")
    rd = RedisClient("redis://localhost:6379/0")

    # Accessing the underlying client before connect() must fail loudly.
    for c, attr in [(pg, "engine"), (qd, "client"), (nj, "driver"), (rd, "client")]:
        try:
            getattr(c, attr)
        except RuntimeError as e:
            assert "not connected" in str(e)
        else:  # pragma: no cover
            raise AssertionError(f"{type(c).__name__}.{attr} should have raised")


def test_clients_satisfy_storage_protocol() -> None:
    clients = [
        PostgresClient("postgresql+asyncpg://x:y@h:5432/db"),
        QdrantStorageClient("http://localhost:6333"),
        Neo4jClient("bolt://localhost:7687", "u", "p"),
        RedisClient("redis://localhost:6379/0"),
    ]
    for c in clients:
        assert isinstance(c, StorageClient)
        assert isinstance(c.name, str) and c.name


def test_storage_health_is_immutable() -> None:
    h = StorageHealth("postgres", ok=True, latency_ms=1.0)
    try:
        h.ok = False  # type: ignore[misc]
    except Exception:
        pass
    else:  # pragma: no cover
        raise AssertionError("StorageHealth should be frozen")
