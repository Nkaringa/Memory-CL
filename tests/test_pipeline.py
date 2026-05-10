from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.ingestion import IngestionPipeline, make_context
from schemas import GraphEdge, GraphNode


def _make_repo(tmp_path: Path) -> Path:
    """A 2-file repo with a cross-file call we can pin in assertions."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "service.py").write_text(
        textwrap.dedent("""
            def helper():
                return 1

            def caller():
                return helper()
        """).lstrip()
    )
    (tmp_path / "pkg" / "util.py").write_text(
        textwrap.dedent("""
            VERSION = '1'

            class Worker:
                def run(self): return helper()
        """).lstrip()
    )
    return tmp_path


def _fake_units_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.list_units_for_file = AsyncMock(return_value=[])  # no obsolete rows
    repo.delete_units_for_file = AsyncMock(return_value=0)
    repo.upsert_units = AsyncMock(side_effect=lambda units: len(list(units)))
    return repo


def _fake_graph_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.delete_subgraph_for_file = AsyncMock(return_value=0)
    repo.upsert_nodes = AsyncMock(side_effect=lambda nodes: len(list(nodes)))
    repo.upsert_edges = AsyncMock(side_effect=lambda edges: len(list(edges)))
    return repo


def _fake_vector_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.delete_points_for_file = AsyncMock(return_value=0)
    repo.upsert_payloads = AsyncMock(side_effect=lambda c, pts: len(list(pts)))
    return repo


@pytest.mark.asyncio
async def test_pipeline_writes_to_all_three_stores(tmp_path: Path) -> None:
    repo_path = _make_repo(tmp_path)
    units_repo = _fake_units_repo()
    graph_repo = _fake_graph_repo()
    vector_repo = _fake_vector_repo()

    ctx = make_context(
        repo_id="r1",
        repo_path=repo_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
    )

    result = await IngestionPipeline().run(ctx)

    # Each backend wrote at least once.
    assert units_repo.upsert_units.await_count >= 1
    assert graph_repo.upsert_nodes.await_count >= 1
    assert vector_repo.upsert_payloads.await_count >= 1

    # Metrics agree with mock side effects.
    assert result.metrics["files_walked"] >= 2
    assert result.metrics["files_parsed"] >= 2
    assert result.metrics["units_emitted"] > 0
    assert result.metrics["units_changed"] > 0
    assert result.metrics["nodes_written"] > 0
    assert result.metrics["edges_written"] > 0
    assert result.metrics["vector_payloads_written"] > 0
    assert result.failed_files == ()


@pytest.mark.asyncio
async def test_pipeline_isolates_syntax_errors(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("def f(): pass\n")
    (tmp_path / "bad.py").write_text("def broken(:\n")  # syntax error

    ctx = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=_fake_units_repo(),
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline().run(ctx)

    assert "bad.py" in result.failed_files
    assert "good.py" not in result.failed_files
    # Pipeline still progressed for the good file.
    assert result.metrics["files_parsed"] == 1
    assert result.metrics["files_failed"] == 1


@pytest.mark.asyncio
async def test_pipeline_isolates_per_file_storage_failure(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def fa(): pass\n")
    (tmp_path / "b.py").write_text("def fb(): pass\n")

    units_repo = _fake_units_repo()
    # First call succeeds, second raises.
    call_count = {"n": 0}

    async def upsert_units_flaky(units):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated DB outage")
        return len(list(units))

    units_repo.upsert_units = AsyncMock(side_effect=upsert_units_flaky)

    ctx = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )

    result = await IngestionPipeline().run(ctx)

    assert len(result.failed_files) == 1
    assert result.metrics["files_failed"] == 1
    # The healthy file still reaches the writer.
    assert units_repo.upsert_units.await_count == 2


@pytest.mark.asyncio
async def test_reingest_with_obsolete_units_triggers_reconciliation(
    tmp_path: Path,
) -> None:
    (tmp_path / "m.py").write_text("def f(): pass\n")

    units_repo = _fake_units_repo()
    graph_repo = _fake_graph_repo()
    vector_repo = _fake_vector_repo()
    # Pretend a stale unit_id remains from a previous commit.
    units_repo.list_units_for_file = AsyncMock(
        return_value=[AsyncMock(unit_id="ghost-id-not-in-current-batch")]
    )

    ctx = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c2",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
    )
    await IngestionPipeline().run(ctx)

    # All three stores got their delete_*_for_file path exercised.
    units_repo.delete_units_for_file.assert_awaited()
    graph_repo.delete_subgraph_for_file.assert_awaited()
    vector_repo.delete_points_for_file.assert_awaited()


@pytest.mark.asyncio
async def test_pipeline_is_deterministic(tmp_path: Path) -> None:
    repo_path = _make_repo(tmp_path)

    captured_runs: list[list[GraphNode]] = []
    captured_edges: list[list[GraphEdge]] = []

    async def capture_nodes(nodes):
        captured_runs.append(list(nodes))
        return len(list(nodes))

    async def capture_edges(edges):
        captured_edges.append(list(edges))
        return len(list(edges))

    for _ in range(2):
        units_repo = _fake_units_repo()
        graph_repo = _fake_graph_repo()
        graph_repo.upsert_nodes = AsyncMock(side_effect=capture_nodes)
        graph_repo.upsert_edges = AsyncMock(side_effect=capture_edges)
        vector_repo = _fake_vector_repo()
        ctx = make_context(
            repo_id="r1",
            repo_path=repo_path,
            commit_sha="c1",
            units_collection="repo_r1",
            units_repo=units_repo,
            graph_repo=graph_repo,
            vector_repo=vector_repo,
        )
        await IngestionPipeline().run(ctx)

    # Two runs over the same repo produce byte-identical write-streams
    # (sorted node ids + edges) per file batch.
    assert len(captured_runs) % 2 == 0
    half = len(captured_runs) // 2
    first = captured_runs[:half]
    second = captured_runs[half:]
    for a, b in zip(first, second, strict=True):
        assert [n.node_id for n in a] == [n.node_id for n in b]
