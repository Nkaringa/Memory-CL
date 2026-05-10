from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from storage import QdrantVectorRepository, VectorPoint, VectorRepository
from storage.qdrant_repo import _payload


def _point(unit_id: str = "u1", **overrides: Any) -> VectorPoint:
    base: dict[str, Any] = {
        "point_id": unit_id,
        "repo_id": "r",
        "qualified_name": "pkg.m.f",
        "kind": "fn",
        "file_path": "pkg/m.py",
        "line_start": 1,
        "line_end": 5,
        "commit_sha": "c1",
        "source_sha": "s1",
    }
    base.update(overrides)
    return VectorPoint(**base)


def test_repository_satisfies_protocol() -> None:
    repo = QdrantVectorRepository(client=AsyncMock())
    assert isinstance(repo, VectorRepository)


def test_payload_keys_are_sorted_and_complete() -> None:
    payload = _payload(_point())
    expected = {
        "commit_sha", "file_path", "has_vector", "kind", "line_end",
        "line_start", "qualified_name", "repo_id", "source_sha",
    }
    assert set(payload.keys()) == expected
    # Determinism: ordered by sorted keys.
    assert list(payload.keys()) == sorted(payload.keys())
    assert payload["has_vector"] is False  # Phase 2 writes payload only.


def test_payload_records_real_vector_when_present() -> None:
    p_with_vec = replace(_point(), vector=(0.1, 0.2))
    assert _payload(p_with_vec)["has_vector"] is True


@pytest.mark.asyncio
async def test_ensure_collection_creates_when_missing() -> None:
    client = AsyncMock()
    client.collection_exists = AsyncMock(return_value=False)
    repo = QdrantVectorRepository(client=client)
    await repo.ensure_collection("repo_r1", vector_size=1536)
    client.create_collection.assert_awaited()
    # Cache primed for placeholder vectors.
    assert repo._size_cache["repo_r1"] == 1536


@pytest.mark.asyncio
async def test_ensure_collection_skips_when_present_but_caches_size() -> None:
    client = AsyncMock()
    client.collection_exists = AsyncMock(return_value=True)
    repo = QdrantVectorRepository(client=client)
    await repo.ensure_collection("repo_r1", vector_size=1536)
    client.create_collection.assert_not_awaited()
    assert repo._size_cache["repo_r1"] == 1536


@pytest.mark.asyncio
async def test_ensure_collection_rejects_invalid_size() -> None:
    repo = QdrantVectorRepository(client=AsyncMock())
    with pytest.raises(ValueError):
        await repo.ensure_collection("c", vector_size=0)


@pytest.mark.asyncio
async def test_upsert_payloads_uses_placeholder_vector_and_sorts_input() -> None:
    client = AsyncMock()
    client.collection_exists = AsyncMock(return_value=False)
    repo = QdrantVectorRepository(client=client)
    await repo.ensure_collection("c", vector_size=4)

    # Two points fed in reverse order — must be reordered by point_id.
    n = await repo.upsert_payloads("c", [_point("u2"), _point("u1")])
    assert n == 2

    _args, kwargs = client.upsert.call_args
    sent_points = kwargs["points"]
    ids = [p.id for p in sent_points]
    assert ids == sorted(ids)
    assert sent_points[0].vector == [0.0, 0.0, 0.0, 0.0]


@pytest.mark.asyncio
async def test_upsert_payload_uses_real_vector_when_provided() -> None:
    client = AsyncMock()
    client.collection_exists = AsyncMock(return_value=False)
    repo = QdrantVectorRepository(client=client)
    await repo.ensure_collection("c", vector_size=2)

    pt = replace(_point(), vector=(0.5, 0.5))
    await repo.upsert_payload("c", pt)
    sent = client.upsert.call_args.kwargs["points"][0]
    assert sent.vector == [0.5, 0.5]
    assert sent.payload["has_vector"] is True


@pytest.mark.asyncio
async def test_delete_points_for_file_uses_filter() -> None:
    client = AsyncMock()
    repo = QdrantVectorRepository(client=client)
    await repo.delete_points_for_file("c", "r", "pkg/m.py")
    _args, kwargs = client.delete.call_args
    selector = kwargs["points_selector"]
    # Filter must include both repo_id and file_path conditions.
    must_keys = {cond.key for cond in selector.filter.must}
    assert must_keys == {"repo_id", "file_path"}


@pytest.mark.asyncio
async def test_upsert_payloads_handles_empty_input() -> None:
    client = AsyncMock()
    repo = QdrantVectorRepository(client=client)
    n = await repo.upsert_payloads("c", [])
    assert n == 0
    client.upsert.assert_not_awaited()
