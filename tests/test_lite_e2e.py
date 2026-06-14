"""Lite mode end-to-end: real ingestion pipeline + retrieval over the
embedded SQLite/numpy/python backends — zero external services.

Uses the DeterministicEmbedder (stable, offline, no model download) so the
test is fast; it proves the lite backends integrate with the actual
pipeline + vector search, which is the wiring that matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.embeddings import ChunkingStrategy, DeterministicEmbedder, EmbeddingPipeline
from core.ingestion import IngestionPipeline, make_context
from storage.lite.engine import make_sqlite_engine
from storage.lite.graph_repo import LiteGraphRepository
from storage.lite.ingestion_repo import SqliteIngestionRepository
from storage.lite.vector_repo import LiteVectorStore

pytestmark = pytest.mark.asyncio

_DIM = 1536
_FIXTURE = '''\
def login(user):
    """Authenticate a user."""
    return check_password(user)


def check_password(user):
    return True


def logout(user):
    return None
'''


async def test_lite_ingest_search_read_end_to_end(tmp_path: Path) -> None:
    # A tiny repo on disk.
    repo_dir = tmp_path / "proj"
    repo_dir.mkdir()
    (repo_dir / "auth.py").write_text(_FIXTURE)

    # Embedded backends sharing one SQLite db.
    engine = make_sqlite_engine(tmp_path / "data.db")
    units = SqliteIngestionRepository(engine)
    graph = LiteGraphRepository(engine)
    vectors = LiteVectorStore(engine)
    for repo in (units, vectors):
        await repo.ensure_schema()
    await graph.ensure_schema()
    await vectors.ensure_collection("repo_proj", _DIM)

    embedder = DeterministicEmbedder(dimension=_DIM)
    embedding_pipeline = EmbeddingPipeline(
        embedder=embedder,
        chunker=ChunkingStrategy(chunk_size=400, chunk_overlap=40),
        vector_repo=vectors,
    )

    ctx = make_context(
        repo_id="proj", repo_path=repo_dir, commit_sha="c0",
        units_collection="repo_proj",
        units_repo=units, graph_repo=graph, vector_repo=vectors,
    )
    result = await IngestionPipeline(embedding_pipeline=embedding_pipeline).run(ctx)
    assert result.metrics["units_emitted"] >= 3  # login, check_password, logout

    # Canonical store (SQLite) has the repo + units.
    repos = {s.repo_id: s for s in await units.list_repos()}
    assert "proj" in repos and repos["proj"].units >= 3
    qnames = {m.qualified_name for m in await units.search_qnames("proj", "login", limit=5)}
    assert any("login" in q for q in qnames)

    # Vector search (numpy): embedding the login unit's text finds it back.
    login_unit = None
    for u in await units.list_units_for_repo("proj"):
        if u.qualified_name.endswith("login"):
            login_unit = u
            break
    assert login_unit is not None
    [qvec] = await embedder.embed_batch([login_unit.content])
    hits = await vectors.search("repo_proj", list(qvec), limit=10)
    # All four real-vector units are searchable; the login unit is among them.
    hit_ids = {h.payload["unit_id"] for h in hits}
    assert len(hits) == 4
    assert login_unit.unit_id in hit_ids
    # Scores are real cosines in [-1, 1].
    assert all(-1.0 <= h.score <= 1.0 for h in hits)

    # Graph (SQLite + BFS): login --CALLS--> check_password edge exists.
    nodes, edges = await graph.repo_graph("proj")
    assert any(kind == "CALLS" for _s, kind, _d in edges)
