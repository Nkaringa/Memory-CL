"""Real tests for the lite numpy/SQLite vector store."""

from __future__ import annotations

from pathlib import Path

import pytest

from storage.lite.engine import make_sqlite_engine
from storage.lite.vector_repo import LiteVectorStore
from storage.repositories import VectorPoint

pytestmark = pytest.mark.asyncio


def _point(pid: str, vector, *, repo_id="r", file_path="m.py") -> VectorPoint:
    return VectorPoint(
        point_id=pid, repo_id=repo_id, qualified_name=pid, kind="function",
        file_path=file_path, line_start=1, line_end=2, commit_sha="c",
        source_sha="s", vector=tuple(vector) if vector is not None else None,
    )


async def _store(tmp_path: Path) -> LiteVectorStore:
    store = LiteVectorStore(make_sqlite_engine(tmp_path / "v.db"))
    await store.ensure_schema()
    await store.ensure_collection("repo_r", 3)
    return store


async def test_has_both_protocol_surfaces(tmp_path: Path) -> None:
    # write-side (VectorRepository) + read-side (VectorSearchClient) methods.
    store = await _store(tmp_path)
    for m in ("ensure_collection", "recreate_collection", "upsert_payload",
              "upsert_payloads", "delete_points_for_file", "search"):
        assert callable(getattr(store, m))


async def test_search_ranks_by_cosine(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    await store.upsert_payloads("repo_r", [
        _point("aligned", [1.0, 0.0, 0.0]),
        _point("close", [0.9, 0.1, 0.0]),
        _point("orthogonal", [0.0, 1.0, 0.0]),
    ])
    hits = await store.search("repo_r", [1.0, 0.0, 0.0], limit=2)
    assert [h.id for h in hits] == ["aligned", "close"]
    assert hits[0].score > hits[1].score
    # payload round-trips with the canonical unit_id.
    assert hits[0].payload["unit_id"] == "aligned"
    assert hits[0].payload["kind"] == "function"


async def test_placeholder_points_excluded_from_search(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    await store.upsert_payloads("repo_r", [
        _point("real", [1.0, 0.0, 0.0]),
        _point("placeholder", None),  # no vector -> has_vector False
    ])
    hits = await store.search("repo_r", [1.0, 0.0, 0.0], limit=10)
    assert {h.id for h in hits} == {"real"}


async def test_upsert_replaces_and_counts(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    assert await store.upsert_payloads("repo_r", [_point("p", [1.0, 0.0, 0.0])]) == 1
    # re-upsert same id with a new vector -> still one row, updated
    await store.upsert_payloads("repo_r", [_point("p", [0.0, 0.0, 1.0])])
    hits = await store.search("repo_r", [0.0, 0.0, 1.0], limit=5)
    assert len(hits) == 1 and hits[0].id == "p" and hits[0].score > 0.99


async def test_delete_points_for_file(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    await store.upsert_payloads("repo_r", [
        _point("a", [1.0, 0.0, 0.0], file_path="x.py"),
        _point("b", [0.0, 1.0, 0.0], file_path="y.py"),
    ])
    assert await store.delete_points_for_file("repo_r", "r", "x.py") == 1
    hits = await store.search("repo_r", [0.0, 1.0, 0.0], limit=5)
    assert {h.id for h in hits} == {"b"}


async def test_recreate_clears_collection(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    await store.upsert_payloads("repo_r", [_point("a", [1.0, 0.0, 0.0])])
    await store.recreate_collection("repo_r", 384)  # new dim, dropped points
    assert await store.search("repo_r", [1.0, 0.0, 0.0], limit=5) == []


async def test_mismatched_dimension_is_skipped(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    await store.upsert_payloads("repo_r", [_point("a", [1.0, 0.0, 0.0])])
    # query of a different dimension -> no crash, just no hits
    assert await store.search("repo_r", [1.0, 0.0], limit=5) == []
