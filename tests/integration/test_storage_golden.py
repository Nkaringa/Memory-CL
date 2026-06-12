"""Golden integration test against REAL postgres/neo4j/qdrant/redis.

Every other storage test in this suite mocks its client. That gap let six
wire-level bugs reach production (Qdrant ":" collection-name rejection,
asyncpg INTEGER/TIMESTAMPTZ CTE coercion, SQLAlchemy comment-bind parsing,
Qdrant hex point-id rejection, Neo4j parameterized var-length depth). This
file ingests the golden fixtures through the SAME client/repo construction
the API lifespan uses, against real containerized stores, so that whole
class of driver-level bugs fails here instead of on a homelab VM.

How to run:

    docker compose -f tests/integration/docker-compose.test.yml up -d --wait
    .venv/bin/pytest -m integration tests/integration/ -v
    docker compose -f tests/integration/docker-compose.test.yml down -v

Without the stack running, every test here skips cleanly, so the default
`pytest tests/` run stays green with no docker daemon at all.

Isolation: each test run uses a unique `golden-*-<pid>` repo_id (and the
matching per-repo Qdrant collection), so leftover rows from a crashed
earlier run — or a concurrent run against the same stack — can never make
counts lie. Tests still delete their own data on the way out so a
long-lived stack doesn't accumulate garbage.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text

from apps.api.lifespan import client_proxy, driver_proxy, engine_proxy
from core.ingestion import IngestionPipeline, make_context
from core.ingestion.pipeline import IngestionResult
from core.parsing import FileWalker, PythonParser, TreeSitterParser
from core.retrieval.graph_retriever import GraphRetriever
from schemas import IngestionUnit, Language, stable_unit_id
from storage import (
    Neo4jClient,
    Neo4jGraphRepository,
    PostgresClient,
    PostgresIngestionRepository,
    QdrantStorageClient,
    QdrantVectorRepository,
    RedisClient,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Test-stack endpoints — must match tests/integration/docker-compose.test.yml.
# ---------------------------------------------------------------------------
POSTGRES_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/memory_test"
NEO4J_URI = "bolt://127.0.0.1:57687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "memory-cl-test"
QDRANT_URL = "http://127.0.0.1:56333"
REDIS_URL = "redis://127.0.0.1:56379/0"

_PORTS = (55432, 57687, 56333, 56379)

FIXTURE_PY = Path(__file__).parent.parent / "fixtures" / "sample_repo"
FIXTURE_JS = Path(__file__).parent.parent / "fixtures" / "sample_repo_js"

COMMIT_SHA = "feedfacefeedfacefeedfacefeedfacefeedface"

# Mirrors apps/api/routers/ingest.py — the Phase-2 pinned dimension.
VECTOR_SIZE = 1536

_HEALTHY_TIMEOUT_S = 90.0  # neo4j cold start dominates


def _stack_reachable() -> bool:
    for port in _PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                pass
        except OSError:
            return False
    return True


@dataclass
class Stores:
    """Real clients + repositories, wired exactly like the API lifespan."""

    postgres: PostgresClient
    qdrant: QdrantStorageClient
    neo4j: Neo4jClient
    redis: RedisClient
    units_repo: PostgresIngestionRepository
    graph_repo: Neo4jGraphRepository
    vector_repo: QdrantVectorRepository


async def _wait_healthy(clients) -> None:
    """TCP-open does not mean ready (docker-proxy accepts early) — poll pings."""
    deadline = time.monotonic() + _HEALTHY_TIMEOUT_S
    while True:
        healths = [await c.ping() for c in clients]
        if all(h.ok for h in healths):
            return
        if time.monotonic() > deadline:
            bad = ", ".join(f"{h.name}: {h.error}" for h in healths if not h.ok)
            pytest.fail(
                f"stores reachable but not healthy within {_HEALTHY_TIMEOUT_S:.0f}s — {bad}"
            )
        await asyncio.sleep(1.0)


@pytest.fixture
async def stores():
    if not _stack_reachable():
        pytest.skip(
            "integration stack not running — start it with: "
            "docker compose -f tests/integration/docker-compose.test.yml up -d --wait"
        )

    pg = PostgresClient(POSTGRES_URL)
    qd = QdrantStorageClient(QDRANT_URL)
    nj = Neo4jClient(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    rd = RedisClient(REDIS_URL)
    # Same lazy-proxy wiring as apps.api.lifespan._build_state — the point
    # of this test is to exercise production construction, not a variant.
    units_repo = PostgresIngestionRepository(engine_proxy(pg))
    graph_repo = Neo4jGraphRepository(driver_proxy(nj))
    vector_repo = QdrantVectorRepository(client_proxy(qd))

    clients = (pg, qd, nj, rd)
    await asyncio.gather(*(c.connect() for c in clients))
    try:
        await _wait_healthy(clients)
        # Same durable bootstrap the lifespan runs once per process.
        await units_repo.ensure_schema()
        await graph_repo.ensure_constraints()
        yield Stores(
            postgres=pg,
            qdrant=qd,
            neo4j=nj,
            redis=rd,
            units_repo=units_repo,
            graph_repo=graph_repo,
            vector_repo=vector_repo,
        )
    finally:
        await asyncio.gather(*(c.disconnect() for c in clients), return_exceptions=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _expected_units(repo_path: Path, repo_id: str) -> list[IngestionUnit]:
    """Independently re-run walk+parse so store counts are checked against
    the parser's exact output, not against the pipeline's own metrics."""
    parsers = {
        Language.PYTHON: PythonParser(),
        Language.JAVASCRIPT: TreeSitterParser(Language.JAVASCRIPT),
        Language.TYPESCRIPT: TreeSitterParser(Language.TYPESCRIPT),
    }
    walk = FileWalker().walk(repo_path, repo_id=repo_id)
    units: list[IngestionUnit] = []
    for ref in walk.files:
        source = (repo_path / ref.path).read_text(encoding="utf-8")
        units.extend(
            parsers[ref.language].parse_file(
                source=source,
                repo_id=repo_id,
                file_path=ref.path,
                commit_sha=COMMIT_SHA,
            )
        )
    return units


async def _ingest(
    stores: Stores, repo_id: str, repo_path: Path
) -> tuple[str, IngestionResult]:
    """Mirror apps/api/routers/ingest.py: ensure collection, run pipeline."""
    collection = f"repo_{repo_id}"
    await stores.vector_repo.ensure_collection(collection, VECTOR_SIZE)
    ctx = make_context(
        repo_id=repo_id,
        repo_path=repo_path,
        commit_sha=COMMIT_SHA,
        units_collection=collection,
        units_repo=stores.units_repo,
        graph_repo=stores.graph_repo,
        vector_repo=stores.vector_repo,
    )
    result = await IngestionPipeline().run(ctx)
    return collection, result


async def _pg_qnames(stores: Stores, repo_id: str) -> set[str]:
    async with stores.postgres.engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT qualified_name FROM ingestion_units WHERE repo_id = :repo_id"),
            {"repo_id": repo_id},
        )
        return {r[0] for r in rows.all()}


async def _pg_unit_count(stores: Stores, repo_id: str) -> int:
    async with stores.postgres.engine.connect() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM ingestion_units WHERE repo_id = :repo_id"),
            {"repo_id": repo_id},
        )
        return int(result.scalar_one())


async def _neo4j_counts(stores: Stores, repo_id: str) -> tuple[int, int]:
    async with stores.neo4j.driver.session() as session:
        result = await session.run(
            "MATCH (n {repo_id: $repo_id}) RETURN count(n) AS c", {"repo_id": repo_id}
        )
        rec = await result.single()
        nodes = int(rec["c"])
        result = await session.run(
            "MATCH ()-[r {repo_id: $repo_id}]->() RETURN count(r) AS c",
            {"repo_id": repo_id},
        )
        rec = await result.single()
        edges = int(rec["c"])
    return nodes, edges


async def _qdrant_point_count(stores: Stores, collection: str) -> int:
    result = await stores.qdrant.client.count(collection_name=collection, exact=True)
    return int(result.count)


async def _cleanup(stores: Stores, repo_id: str, collection: str) -> None:
    async with stores.postgres.engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM ingestion_units WHERE repo_id = :repo_id"),
            {"repo_id": repo_id},
        )
    async with stores.neo4j.driver.session() as session:
        await session.run(
            "MATCH (n {repo_id: $repo_id}) DETACH DELETE n", {"repo_id": repo_id}
        )
    if await stores.qdrant.client.collection_exists(collection_name=collection):
        await stores.qdrant.client.delete_collection(collection_name=collection)


# ---------------------------------------------------------------------------
# The golden tests
# ---------------------------------------------------------------------------
async def test_golden_python_repo_roundtrip(stores: Stores) -> None:
    repo_id = f"golden-py-{os.getpid()}"
    collection = f"repo_{repo_id}"
    try:
        # All four backends answered a real ping before we got here; assert
        # redis explicitly since nothing else in the write path touches it.
        assert (await stores.redis.ping()).ok

        collection, result = await _ingest(stores, repo_id, FIXTURE_PY)
        assert result.failed_files == ()

        expected = _expected_units(FIXTURE_PY, repo_id)
        assert len(expected) > 0
        assert result.metrics["units_emitted"] == len(expected)

        # Postgres rows match the parser's output exactly (count AND qnames).
        assert await _pg_unit_count(stores, repo_id) == len(expected)
        assert await _pg_qnames(stores, repo_id) == {u.qualified_name for u in expected}

        nodes, edges = await _neo4j_counts(stores, repo_id)
        assert nodes > 0
        assert edges > 0

        # Qdrant: collection exists, one point per unit.
        assert await stores.qdrant.client.collection_exists(collection_name=collection)
        assert await _qdrant_point_count(stores, collection) == len(expected)

        # query_graph-equivalent: GraphRetriever over the REAL Neo4j repo,
        # seeded by a qname known to exist in the fixture. unit_id is
        # deterministic (sha256 of repo:path:qname), so no lookup needed.
        login_id = stable_unit_id(
            repo_id, "pkg/services/auth.py", "pkg.services.auth.login"
        )
        neighbors = await stores.graph_repo.neighbors(login_id, depth=2)
        assert neighbors, "neighbors() returned nothing for pkg.services.auth.login"

        candidates = await GraphRetriever(stores.graph_repo, max_depth=2).search(
            [login_id], repo_id=repo_id
        )
        assert len(candidates) > 1  # seed plus at least one real neighbor
        qnames = {c.qualified_name for c in candidates}
        # `refresh` lives in the same file: login -(File node)- refresh at depth 2.
        assert "pkg.services.auth.refresh" in qnames

        # Idempotency: re-ingesting the same content changes nothing.
        _, again = await _ingest(stores, repo_id, FIXTURE_PY)
        assert again.failed_files == ()
        assert again.metrics["units_changed"] == 0
        assert await _pg_unit_count(stores, repo_id) == len(expected)
        assert await _neo4j_counts(stores, repo_id) == (nodes, edges)
        assert await _qdrant_point_count(stores, collection) == len(expected)
    finally:
        await _cleanup(stores, repo_id, collection)


async def test_golden_js_ts_repo_roundtrip(stores: Stores) -> None:
    """Same round-trip through the tree-sitter (JS/TS) parsing path."""
    repo_id = f"golden-js-{os.getpid()}"
    collection = f"repo_{repo_id}"
    try:
        collection, result = await _ingest(stores, repo_id, FIXTURE_JS)
        assert result.failed_files == ()

        expected = _expected_units(FIXTURE_JS, repo_id)
        assert len(expected) > 0
        expected_qnames = {u.qualified_name for u in expected}
        # Sanity-pin the fixture shape so a silently-empty tree-sitter
        # parse can't pass on counts alone.
        assert {
            "mathUtils.multiply",
            "calculator.Calculator",
            "calculator.Calculator.add",
            "logger.logCall",
        } <= expected_qnames

        assert await _pg_unit_count(stores, repo_id) == len(expected)
        assert await _pg_qnames(stores, repo_id) == expected_qnames

        nodes, edges = await _neo4j_counts(stores, repo_id)
        assert nodes > 0
        assert edges > 0

        assert await stores.qdrant.client.collection_exists(collection_name=collection)
        assert await _qdrant_point_count(stores, collection) == len(expected)

        calc_id = stable_unit_id(repo_id, "calculator.ts", "calculator.Calculator")
        assert await stores.graph_repo.neighbors(calc_id, depth=2)

        # Idempotency through the tree-sitter path too.
        _, again = await _ingest(stores, repo_id, FIXTURE_JS)
        assert again.failed_files == ()
        assert again.metrics["units_changed"] == 0
        assert await _pg_unit_count(stores, repo_id) == len(expected)
        assert await _neo4j_counts(stores, repo_id) == (nodes, edges)
    finally:
        await _cleanup(stores, repo_id, collection)
