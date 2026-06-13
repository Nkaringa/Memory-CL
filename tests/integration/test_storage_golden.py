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
from core.ingestion.graph_builder import _UNIT_TO_NODE, GraphBuilder
from core.ingestion.pipeline import IngestionResult
from core.parsing import DocParser, FileWalker, PythonParser, TreeSitterParser
from core.retrieval.graph_retriever import GraphRetriever
from schemas import IngestionUnit, Language, stable_unit_id
from storage import (
    AppConfigRepository,
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
FIXTURE_CSHARP = Path(__file__).parent.parent / "fixtures" / "sample_repo_csharp"
FIXTURE_GO = Path(__file__).parent.parent / "fixtures" / "sample_repo_go"
FIXTURE_JAVA = Path(__file__).parent.parent / "fixtures" / "sample_repo_java"
FIXTURE_RUST = Path(__file__).parent.parent / "fixtures" / "sample_repo_rust"
FIXTURE_DOCS = Path(__file__).parent.parent / "fixtures" / "sample_repo_docs"

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
        Language.CSHARP: TreeSitterParser(Language.CSHARP),
        Language.GO: TreeSitterParser(Language.GO),
        Language.JAVA: TreeSitterParser(Language.JAVA),
        Language.RUST: TreeSitterParser(Language.RUST),
        Language.MARKDOWN: DocParser(Language.MARKDOWN),
        Language.TEXT: DocParser(Language.TEXT),
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


def _expected_edge_count(units: list[IngestionUnit]) -> int:
    """Independently re-derive the run-wide edge set the pipeline builds
    (per-file GraphBuilder over the FULL qname resolver, deduplicated),
    so the Neo4j edge count can be checked EXACTLY. The pre-fix pipeline
    silently dropped forward cross-file edges (MATCH…MERGE against
    not-yet-written nodes) while still reporting len(edges) as written —
    an exact-count assertion catches both halves of that bug."""
    resolver = {u.qualified_name: (u.unit_id, _UNIT_TO_NODE[u.kind]) for u in units}
    by_file: dict[str, list[IngestionUnit]] = {}
    for u in units:
        by_file.setdefault(u.file_path, []).append(u)
    builder = GraphBuilder()
    edges: set[tuple[str, str, str]] = set()
    for path in sorted(by_file):
        result = builder.build(by_file[path], qname_resolver=resolver)
        edges |= {(e.src_id, e.kind.value, e.dst_id) for e in result.edges}
    return len(edges)


async def _ingest(stores: Stores, repo_id: str, repo_path: Path) -> tuple[str, IngestionResult]:
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
        await session.run("MATCH (n {repo_id: $repo_id}) DETACH DELETE n", {"repo_id": repo_id})
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

        # NK-Base regression: NO edge may be silently dropped. The walk is
        # alphabetical (pkg/services/auth.py before pkg/utils.py), so any
        # per-file edge write would race ahead of its targets; the final
        # edge pass must land the EXACT re-derived run-wide edge set, and
        # edges_written must be the store's honest count of it.
        expected_edges = _expected_edge_count(expected)
        assert edges == expected_edges, (
            f"Neo4j has {edges} edges, expected {expected_edges} — edges dropped"
        )
        assert result.metrics["edges_written"] == expected_edges

        # Qdrant: collection exists, one point per unit.
        assert await stores.qdrant.client.collection_exists(collection_name=collection)
        assert await _qdrant_point_count(stores, collection) == len(expected)

        # query_graph-equivalent: GraphRetriever over the REAL Neo4j repo,
        # seeded by a qname known to exist in the fixture. unit_id is
        # deterministic (sha256 of repo:path:qname), so no lookup needed.
        login_id = stable_unit_id(repo_id, "pkg/services/auth.py", "pkg.services.auth.login")
        neighbors = await stores.graph_repo.neighbors(login_id, depth=2)
        assert neighbors, "neighbors() returned nothing for pkg.services.auth.login"

        candidates = await GraphRetriever(stores.graph_repo, max_depth=2).search(
            [login_id], repo_id=repo_id
        )
        assert len(candidates) > 1  # seed plus at least one real neighbor
        qnames = {c.qualified_name for c in candidates}
        # `refresh` lives in the same file: login -(File node)- refresh at depth 2.
        assert "pkg.services.auth.refresh" in qnames

        # repo_graph: whole-repo snapshot via the new endpoint path.
        # Verifies that the repo_id range indexes bootstrap correctly on
        # real Neo4j and that the graph contains nodes+edges with externals
        # excluded (default). Truncation must be false at the fixture scale.
        rg_nodes, rg_edges = await stores.graph_repo.repo_graph(
            repo_id, include_external=False, max_nodes=20_000
        )
        assert len(rg_nodes) > 0, "repo_graph returned no nodes"
        assert len(rg_edges) > 0, "repo_graph returned no edges"
        # Every returned node must belong to this repo and be non-External.
        from schemas import NodeKind
        for n in rg_nodes:
            assert n.repo_id == repo_id, f"node {n.node_id} has wrong repo_id"
            assert n.kind != NodeKind.EXTERNAL, "External node leaked into non-external graph"
        # Result count must be consistent with the raw Neo4j node count
        # (minus any External nodes that were stamped with this repo_id).
        assert len(rg_nodes) <= nodes

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

        # Exact edge-set check through the tree-sitter path too (covers
        # the cross-file mathUtils -> logger import).
        expected_edges = _expected_edge_count(expected)
        assert edges == expected_edges
        assert result.metrics["edges_written"] == expected_edges

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


# ---------------------------------------------------------------------------
# Batch-2 language round-trips (C#, Go, Java, Rust)
# ---------------------------------------------------------------------------
# Each entry: (short_tag, fixture_path, known_qname, file_path_for_id)
#
# The qname seeds are chosen because the method/function is defined in the
# same file as its parent type, guaranteeing at least one neighbour edge.
#
#   C#   : Geometry/Shapes.cs  -> Circle class -> Circle.Area method
#   Go   : server/handler.go   -> Handler struct -> Handler.Greet method
#   Java : Dog.java            -> Dog class -> Dog.speak method
#   Rust : geometry.rs         -> Point struct -> Point.origin method
#
# All qnames are path-based (design D-16) — no package/namespace prefix.
_BATCH2_CASES: list[tuple[str, Path, str, str, str]] = [
    (
        "cs",
        FIXTURE_CSHARP,
        "Geometry.Shapes.Circle.Area",
        "Geometry/Shapes.cs",
        "Geometry.Shapes.Circle.Area",
    ),
    (
        "go",
        FIXTURE_GO,
        "server.handler.Handler.Greet",
        "server/handler.go",
        "server.handler.Handler.Greet",
    ),
    (
        "java",
        FIXTURE_JAVA,
        "Dog.Dog.speak",
        "Dog.java",
        "Dog.Dog.speak",
    ),
    (
        "rust",
        FIXTURE_RUST,
        "geometry.Point.origin",
        "geometry.rs",
        "geometry.Point.origin",
    ),
]


@pytest.mark.parametrize(
    "tag,fixture_path,anchor_qname,anchor_file,anchor_qname_for_id",
    _BATCH2_CASES,
    ids=[c[0] for c in _BATCH2_CASES],
)
async def test_golden_batch2_language_roundtrip(
    stores: Stores,
    tag: str,
    fixture_path: Path,
    anchor_qname: str,
    anchor_file: str,
    anchor_qname_for_id: str,
) -> None:
    """Ingest a single-language fixture repo through the REAL pipeline and
    assert: no failures, parser==Postgres==Qdrant unit count, Neo4j has
    nodes+edges, and a known qname resolves to non-empty neighbours."""
    repo_id = f"golden-{tag}-{os.getpid()}"
    collection = f"repo_{repo_id}"
    try:
        collection, result = await _ingest(stores, repo_id, fixture_path)
        assert result.failed_files == (), (
            f"{tag}: pipeline reported failures: {result.failed_files}"
        )

        expected = _expected_units(fixture_path, repo_id)
        assert len(expected) > 0, f"{tag}: parser produced zero units"

        pg_count = await _pg_unit_count(stores, repo_id)
        qdrant_count = await _qdrant_point_count(stores, collection)
        assert pg_count == len(expected), (
            f"{tag}: Postgres count {pg_count} != parser count {len(expected)}"
        )
        assert qdrant_count == len(expected), (
            f"{tag}: Qdrant count {qdrant_count} != parser count {len(expected)}"
        )

        nodes, edges = await _neo4j_counts(stores, repo_id)
        assert nodes > 0, f"{tag}: Neo4j has no nodes"
        assert edges > 0, f"{tag}: Neo4j has no edges"

        # Graph traversal: the anchor qname must resolve to at least one
        # neighbour (the parent type or a sibling member shares a File node).
        anchor_id = stable_unit_id(repo_id, anchor_file, anchor_qname_for_id)
        neighbours = await stores.graph_repo.neighbors(anchor_id, depth=2)
        assert neighbours, f"{tag}: neighbors() returned nothing for {anchor_qname_for_id!r}"
    finally:
        await _cleanup(stores, repo_id, collection)


# ---------------------------------------------------------------------------
# Docs ingestion round-trip (markdown sections + link edges)
# ---------------------------------------------------------------------------
async def test_golden_docs_repo_roundtrip(stores: Stores) -> None:
    """Markdown/text fixture through the REAL pipeline: section units land
    in all three stores, Section nodes exist in Neo4j, and a relative
    markdown link produces a Module-IMPORTS->Module edge."""
    repo_id = f"golden-docs-{os.getpid()}"
    collection = f"repo_{repo_id}"
    try:
        collection, result = await _ingest(stores, repo_id, FIXTURE_DOCS)
        assert result.failed_files == ()

        expected = _expected_units(FIXTURE_DOCS, repo_id)
        assert len(expected) > 0
        expected_qnames = {u.qualified_name for u in expected}
        # Pin the fixture shape: 3 README sections, nested guide section,
        # whole-file txt module. A silently-sectionless parse cannot pass.
        assert {
            "README",
            "README.overview",
            "README.installation",
            "README.usage",
            "docs.guide",
            "docs.guide.guide",
            "docs.guide.guide.database",
            "notes",
        } == expected_qnames

        assert await _pg_unit_count(stores, repo_id) == len(expected)
        assert await _pg_qnames(stores, repo_id) == expected_qnames

        nodes, edges = await _neo4j_counts(stores, repo_id)
        assert nodes > 0
        assert edges > 0
        expected_edges = _expected_edge_count(expected)
        assert edges == expected_edges
        assert result.metrics["edges_written"] == expected_edges

        assert await stores.qdrant.client.collection_exists(collection_name=collection)
        assert await _qdrant_point_count(stores, collection) == len(expected)

        # A Section node exists in Neo4j under its own label.
        overview_id = stable_unit_id(repo_id, "README.md", "README.overview")
        async with stores.neo4j.driver.session() as session:
            res = await session.run(
                "MATCH (n:Section {repo_id: $repo_id, node_id: $node_id}) "
                "RETURN count(n) AS c",
                {"repo_id": repo_id, "node_id": overview_id},
            )
            rec = await res.single()
            assert int(rec["c"]) == 1, "README.overview Section node missing"

            # The markdown link edge: README module -IMPORTS-> docs.guide
            # module (relative `docs/guide.md` target resolved cross-file).
            readme_id = stable_unit_id(repo_id, "README.md", "README")
            guide_id = stable_unit_id(repo_id, "docs/guide.md", "docs.guide")
            res = await session.run(
                "MATCH (a {node_id: $src})-[r:IMPORTS]->(b {node_id: $dst}) "
                "RETURN count(r) AS c",
                {"src": readme_id, "dst": guide_id},
            )
            rec = await res.single()
            assert int(rec["c"]) == 1, "README -IMPORTS-> docs.guide edge missing"

        # Graph traversal works from a section unit.
        assert await stores.graph_repo.neighbors(overview_id, depth=2)

        # Idempotency through the docs path too.
        _, again = await _ingest(stores, repo_id, FIXTURE_DOCS)
        assert again.failed_files == ()
        assert again.metrics["units_changed"] == 0
        assert await _pg_unit_count(stores, repo_id) == len(expected)
        assert await _neo4j_counts(stores, repo_id) == (nodes, edges)
    finally:
        await _cleanup(stores, repo_id, collection)


# ---------------------------------------------------------------------------
# app_config runtime-config table — bootstrap + seed-from-env (no-lockout)
# ---------------------------------------------------------------------------
async def test_golden_app_config_bootstrap_and_roundtrip(stores: Stores) -> None:
    """The app_config table bootstraps on real Postgres and the BOOLEAN +
    TIMESTAMPTZ CAST discipline survives a real asyncpg round-trip (the
    same class of bug the unit count tests catch for ingestion_units)."""
    repo = AppConfigRepository(stores.postgres.engine)
    try:
        await repo.ensure_schema()  # idempotent
        await repo.ensure_schema()  # second call must be a no-op

        # Empty on a fresh table (cleaned in finally).
        async with stores.postgres.engine.begin() as conn:
            await conn.execute(text("DELETE FROM app_config"))
        assert await repo.get() is None

        # Upsert with a BOOLEAN true + TIMESTAMPTZ stamp — exercises the
        # CTE CAST path end to end.
        row = await repo.upsert(
            mcp_api_key="golden-mcp-key",
            openai_api_key="sk-golden",
            embedding_mode="openai",
            onboarding_completed=True,
        )
        assert row.mcp_api_key == "golden-mcp-key"
        assert row.onboarding_completed is True
        assert row.updated_at is not None

        fetched = await repo.get()
        assert fetched is not None
        assert fetched.openai_api_key == "sk-golden"
        assert fetched.onboarding_completed is True

        # Single-row invariant: a second upsert updates, never inserts a
        # second row.
        await repo.set_mcp_api_key("rotated-key")
        async with stores.postgres.engine.connect() as conn:
            count = await conn.execute(text("SELECT COUNT(*) FROM app_config"))
            assert int(count.scalar_one()) == 1
        again = await repo.get()
        assert again is not None
        assert again.mcp_api_key == "rotated-key"
        assert again.openai_api_key == "sk-golden"  # preserved across upsert
    finally:
        async with stores.postgres.engine.begin() as conn:
            await conn.execute(text("DELETE FROM app_config"))


async def test_golden_seed_from_env_is_idempotent(stores: Stores) -> None:
    """The seed-on-first-boot path against real Postgres: empty table +
    env keys → seeded; a second seed (or any populated row) → never
    overwritten. This is the no-lockout guarantee for the live VM."""
    from apps.api.lifespan import _seed_app_config_from_env
    from core.config import Settings

    repo = AppConfigRepository(stores.postgres.engine)
    try:
        await repo.ensure_schema()
        async with stores.postgres.engine.begin() as conn:
            await conn.execute(text("DELETE FROM app_config"))

        settings = Settings(mcp_api_key="live-env-key", openai_api_key="sk-live-env")  # type: ignore[arg-type]
        seeded = await _seed_app_config_from_env(repo, settings)
        assert seeded is True
        row = await repo.get()
        assert row is not None
        assert row.mcp_api_key == "live-env-key"
        assert row.openai_api_key == "sk-live-env"

        # Second boot: row exists → seed is a no-op, env does NOT clobber a
        # subsequently-rotated key.
        await repo.set_mcp_api_key("operator-rotated")
        seeded_again = await _seed_app_config_from_env(repo, settings)
        assert seeded_again is False
        final = await repo.get()
        assert final is not None
        assert final.mcp_api_key == "operator-rotated"
    finally:
        async with stores.postgres.engine.begin() as conn:
            await conn.execute(text("DELETE FROM app_config"))
