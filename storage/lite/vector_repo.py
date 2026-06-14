"""Embedded vector store for lite mode — SQLite + brute-force numpy cosine.

One object plays BOTH roles Qdrant plays in server mode:
  * the write-side `VectorRepository` Protocol (ensure/recreate_collection,
    upsert_payload(s), delete_points_for_file), and
  * the read-side `VectorSearchClient` the retriever depends on
    (`search(collection_name, query_vector, limit, ...)`).

Vectors + payloads live in a SQLite table; search loads the collection's
real-vector rows into a numpy matrix and ranks by cosine. At lite's ≤100k
unit ceiling a full scan is milliseconds — no index needed. The payload
schema is byte-identical to the Qdrant one so retrieval behaves the same.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from storage.repositories import VectorPoint

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS lite_vectors (
        collection  TEXT NOT NULL,
        point_id    TEXT NOT NULL,
        vector      TEXT NOT NULL,
        payload     TEXT NOT NULL,
        has_vector  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (collection, point_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lite_collections (
        collection  TEXT PRIMARY KEY,
        vector_size INTEGER NOT NULL
    )
    """,
)


@dataclass(frozen=True)
class LiteHit:
    """A search hit shaped like the retriever's `_id_of/_score_of/_payload_of`
    accessors expect (object attributes)."""

    id: str
    score: float
    payload: dict[str, Any]


def _payload_of(point: VectorPoint) -> dict[str, Any]:
    """Identical payload schema to the Qdrant repo (sorted keys)."""
    raw: dict[str, Any] = {
        "repo_id": point.repo_id,
        "qualified_name": point.qualified_name,
        "kind": point.kind,
        "file_path": point.file_path,
        "line_start": point.line_start,
        "line_end": point.line_end,
        "commit_sha": point.commit_sha,
        "source_sha": point.source_sha,
        "has_vector": point.vector is not None,
        "unit_id": point.point_id,
    }
    return {k: raw[k] for k in sorted(raw)}


class LiteVectorStore:
    name: str = "lite_vector_store"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._size: dict[str, int] = {}

    async def ensure_schema(self) -> None:
        async with self._engine.begin() as conn:
            for stmt in _DDL:
                await conn.execute(text(stmt))

    # ----- VectorRepository (write) -----
    async def ensure_collection(self, name: str, vector_size: int) -> None:
        if vector_size <= 0:
            raise ValueError("vector_size must be > 0")
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO lite_collections (collection, vector_size) "
                    "VALUES (:c, :s) ON CONFLICT(collection) DO NOTHING"
                ),
                {"c": name, "s": vector_size},
            )
        self._size[name] = vector_size

    async def recreate_collection(self, name: str, vector_size: int) -> None:
        if vector_size <= 0:
            raise ValueError("vector_size must be > 0")
        async with self._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM lite_vectors WHERE collection = :c"), {"c": name}
            )
            await conn.execute(
                text(
                    "INSERT INTO lite_collections (collection, vector_size) "
                    "VALUES (:c, :s) ON CONFLICT(collection) DO UPDATE SET "
                    "vector_size = excluded.vector_size"
                ),
                {"c": name, "s": vector_size},
            )
        self._size[name] = vector_size

    async def _collection_size(self, name: str) -> int:
        if name in self._size:
            return self._size[name]
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                text("SELECT vector_size FROM lite_collections WHERE collection = :c"),
                {"c": name},
            )).first()
        size = int(row[0]) if row else 0
        self._size[name] = size
        return size

    async def upsert_payload(self, collection: str, point: VectorPoint) -> None:
        await self.upsert_payloads(collection, [point])

    async def upsert_payloads(
        self, collection: str, points: Iterable[VectorPoint]
    ) -> int:
        points = list(points)
        if not points:
            return 0
        size = await self._collection_size(collection)
        rows = []
        for p in points:
            if p.vector is not None:
                vec = list(p.vector)
                has = 1
            else:
                vec = [0.0] * size
                has = 0
            rows.append({
                "collection": collection,
                "point_id": p.point_id,
                "vector": json.dumps(vec),
                "payload": json.dumps(_payload_of(p)),
                "has_vector": has,
            })
        async with self._engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO lite_vectors "
                    "(collection, point_id, vector, payload, has_vector) "
                    "VALUES (:collection, :point_id, :vector, :payload, :has_vector) "
                    "ON CONFLICT(collection, point_id) DO UPDATE SET "
                    "vector = excluded.vector, payload = excluded.payload, "
                    "has_vector = excluded.has_vector"
                ),
                rows,
            )
        return len(points)

    async def delete_points_for_file(
        self, collection: str, repo_id: str, file_path: str
    ) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    "DELETE FROM lite_vectors WHERE collection = :c "
                    "AND json_extract(payload, '$.repo_id') = :repo "
                    "AND json_extract(payload, '$.file_path') = :fp"
                ),
                {"c": collection, "repo": repo_id, "fp": file_path},
            )
        return result.rowcount or 0

    # ----- VectorSearchClient (read) -----
    async def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        limit: int,
        query_filter: object | None = None,
        with_payload: bool = True,
    ) -> Sequence[LiteHit]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                text(
                    "SELECT point_id, vector, payload FROM lite_vectors "
                    "WHERE collection = :c AND has_vector = 1"
                ),
                {"c": collection_name},
            )).fetchall()
        if not rows:
            return []
        q = np.asarray(list(query_vector), dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn == 0.0:
            return []
        q = q / qn

        ids: list[str] = []
        payloads: list[dict[str, Any]] = []
        vecs: list[list[float]] = []
        for r in rows:
            vec = json.loads(r[1])
            if len(vec) != q.shape[0]:
                continue  # dimension mismatch (e.g. mode switched) — skip
            ids.append(r[0])
            payloads.append(json.loads(r[2]))
            vecs.append(vec)
        if not vecs:
            return []
        matrix = np.asarray(vecs, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0.0] = 1.0
        scores = (matrix @ q) / norms  # cosine (q already unit-norm)

        k = min(max(limit, 1), len(ids))
        top = np.argsort(-scores)[:k]
        return [
            LiteHit(id=ids[i], score=float(scores[i]), payload=payloads[i])
            for i in top
        ]


__all__ = ["LiteHit", "LiteVectorStore"]
