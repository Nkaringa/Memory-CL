from __future__ import annotations

import textwrap
from collections.abc import Iterable, Sequence
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.ingestion import IngestionPipeline, make_context
from core.ingestion.pipeline import _resolve_qname_collisions
from schemas import GraphEdge, GraphNode, IngestionUnit, UnitKind, stable_unit_id
from schemas.ingest import Language, content_sha


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


# ---- Phase 3: embedding wiring ---------------------------------------------
class _RecordingEmbeddingPipeline:
    """Stand-in for EmbeddingPipeline that records every run() call."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[list[IngestionUnit], str]] = []
        self._fail = fail

    async def run(
        self, units: Sequence[IngestionUnit], *, collection: str
    ) -> None:
        self.calls.append((list(units), collection))
        if self._fail:
            raise RuntimeError("simulated provider outage")


def _embedded_unit_ids(pipe: _RecordingEmbeddingPipeline) -> set[str]:
    return {u.unit_id for units, _ in pipe.calls for u in units}


@pytest.mark.asyncio
async def test_pipeline_embeds_all_units_on_first_ingest(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(): return 1\n\ndef g(): return f()\n")
    units_repo = _fake_units_repo()
    captured: list[IngestionUnit] = []

    async def _capture(units: Iterable[IngestionUnit]) -> int:
        batch = list(units)
        captured.extend(batch)
        return len(batch)

    units_repo.upsert_units = AsyncMock(side_effect=_capture)
    embed_pipe = _RecordingEmbeddingPipeline()

    ctx = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline(
        embedding_pipeline=embed_pipe,  # type: ignore[arg-type]
    ).run(ctx)

    # Fresh repo: every unit is new, so every unit gets embedded.
    assert _embedded_unit_ids(embed_pipe) == {u.unit_id for u in captured}
    assert all(coll == "repo_r1" for _, coll in embed_pipe.calls)
    assert result.metrics["units_embedded"] == len(captured)
    assert result.failed_files == ()


@pytest.mark.asyncio
async def test_pipeline_embeds_only_changed_units(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(): return 1\n\ndef g(): return f()\n")
    units_repo = _fake_units_repo()
    captured: list[IngestionUnit] = []

    async def _capture(units: Iterable[IngestionUnit]) -> int:
        batch = list(units)
        captured.extend(batch)
        return len(batch)

    units_repo.upsert_units = AsyncMock(side_effect=_capture)
    # Run 1 (no embedder) just captures the real units + their shas.
    ctx = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )
    await IngestionPipeline().run(ctx)
    assert captured

    # Run 2: existing rows match run 1 except `g`, whose stored sha is
    # stale — only `g` may be embedded.
    existing = [
        u if u.name != "g" else u.model_copy(update={"source_sha": "stale"})
        for u in captured
    ]
    changed_id = next(u.unit_id for u in captured if u.name == "g")
    units_repo2 = _fake_units_repo()
    units_repo2.list_units_for_file = AsyncMock(
        side_effect=lambda repo_id, fp: [u for u in existing if u.file_path == fp]
    )
    embed_pipe = _RecordingEmbeddingPipeline()
    ctx2 = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c2",
        units_collection="repo_r1",
        units_repo=units_repo2,
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline(
        embedding_pipeline=embed_pipe,  # type: ignore[arg-type]
    ).run(ctx2)

    assert _embedded_unit_ids(embed_pipe) == {changed_id}
    assert result.metrics["units_embedded"] == 1


@pytest.mark.asyncio
async def test_pipeline_skips_embedding_when_nothing_changed(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(): return 1\n")
    units_repo = _fake_units_repo()
    captured: list[IngestionUnit] = []

    async def _capture(units: Iterable[IngestionUnit]) -> int:
        batch = list(units)
        captured.extend(batch)
        return len(batch)

    units_repo.upsert_units = AsyncMock(side_effect=_capture)
    ctx = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )
    await IngestionPipeline().run(ctx)

    units_repo2 = _fake_units_repo()
    units_repo2.list_units_for_file = AsyncMock(
        side_effect=lambda repo_id, fp: [u for u in captured if u.file_path == fp]
    )
    embed_pipe = _RecordingEmbeddingPipeline()
    ctx2 = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo2,
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline(
        embedding_pipeline=embed_pipe,  # type: ignore[arg-type]
    ).run(ctx2)

    assert embed_pipe.calls == []
    assert result.metrics["units_embedded"] == 0


@pytest.mark.asyncio
async def test_pipeline_without_embedder_reports_zero_embedded(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(): return 1\n")
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

    assert result.failed_files == ()
    assert result.metrics["units_embedded"] == 0
    assert result.metrics["vector_payloads_written"] > 0


@pytest.mark.asyncio
async def test_pipeline_embed_failure_does_not_fail_ingest(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(): return 1\n")
    units_repo = _fake_units_repo()
    vector_repo = _fake_vector_repo()
    embed_pipe = _RecordingEmbeddingPipeline(fail=True)

    ctx = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=_fake_graph_repo(),
        vector_repo=vector_repo,
    )
    result = await IngestionPipeline(
        embedding_pipeline=embed_pipe,  # type: ignore[arg-type]
    ).run(ctx)

    # Embedding was attempted and blew up — ingest still succeeds and
    # the placeholder points remain (degraded, not failed).
    assert embed_pipe.calls
    assert result.failed_files == ()
    assert result.metrics["files_failed"] == 0
    assert result.metrics["units_embedded"] == 0
    assert result.metrics["vector_payloads_written"] > 0
    assert vector_repo.upsert_payloads.await_count >= 1


def _placeholder_point_ids(vector_repo: AsyncMock) -> set[str]:
    """Every point_id passed to upsert_payloads across all calls."""
    ids: set[str] = set()
    for call in vector_repo.upsert_payloads.await_args_list:
        ids |= {p.point_id for p in call.args[1]}
    return ids


async def _first_run_capture(
    tmp_path: Path,
) -> list[IngestionUnit]:
    """Run the pipeline once (no embedder) and capture the real units."""
    captured: list[IngestionUnit] = []
    units_repo = _fake_units_repo()

    async def _capture(units: Iterable[IngestionUnit]) -> int:
        batch = list(units)
        captured.extend(batch)
        return len(batch)

    units_repo.upsert_units = AsyncMock(side_effect=_capture)
    ctx = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )
    await IngestionPipeline().run(ctx)
    assert captured
    return captured


@pytest.mark.asyncio
async def test_reingest_unchanged_never_rewrites_placeholder_points(
    tmp_path: Path,
) -> None:
    """C1 regression: re-ingesting identical content with an embedding
    pipeline wired must NOT upsert placeholder points — Qdrant upsert
    replaces whole points, so a placeholder write would zero out the
    real vectors of every unchanged unit."""
    (tmp_path / "m.py").write_text("def f(): return 1\n\ndef g(): return f()\n")
    captured = await _first_run_capture(tmp_path)

    units_repo2 = _fake_units_repo()
    units_repo2.list_units_for_file = AsyncMock(
        side_effect=lambda repo_id, fp: [u for u in captured if u.file_path == fp]
    )
    vector_repo2 = _fake_vector_repo()
    embed_pipe = _RecordingEmbeddingPipeline()
    ctx2 = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo2,
        graph_repo=_fake_graph_repo(),
        vector_repo=vector_repo2,
    )
    result = await IngestionPipeline(
        embedding_pipeline=embed_pipe,  # type: ignore[arg-type]
    ).run(ctx2)

    # Nothing changed → zero placeholder upserts AND zero embed calls.
    vector_repo2.upsert_payloads.assert_not_awaited()
    assert embed_pipe.calls == []
    assert result.metrics["vector_payloads_written"] == 0
    assert result.metrics["units_embedded"] == 0


@pytest.mark.asyncio
async def test_reingest_one_changed_unit_placeholders_and_embeds_only_it(
    tmp_path: Path,
) -> None:
    """C1: with an embedding pipeline wired, only the changed unit gets
    a placeholder upsert (and an embedding) — unchanged units' real
    vectors stay untouched."""
    (tmp_path / "m.py").write_text("def f(): return 1\n\ndef g(): return f()\n")
    captured = await _first_run_capture(tmp_path)

    # Existing rows match run 1 except `g`, whose stored sha is stale.
    existing = [
        u if u.name != "g" else u.model_copy(update={"source_sha": "stale"})
        for u in captured
    ]
    changed_id = next(u.unit_id for u in captured if u.name == "g")
    units_repo2 = _fake_units_repo()
    units_repo2.list_units_for_file = AsyncMock(
        side_effect=lambda repo_id, fp: [u for u in existing if u.file_path == fp]
    )
    vector_repo2 = _fake_vector_repo()
    embed_pipe = _RecordingEmbeddingPipeline()
    ctx2 = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c2",
        units_collection="repo_r1",
        units_repo=units_repo2,
        graph_repo=_fake_graph_repo(),
        vector_repo=vector_repo2,
    )
    result = await IngestionPipeline(
        embedding_pipeline=embed_pipe,  # type: ignore[arg-type]
    ).run(ctx2)

    assert _placeholder_point_ids(vector_repo2) == {changed_id}
    assert _embedded_unit_ids(embed_pipe) == {changed_id}
    assert result.metrics["units_embedded"] == 1


@pytest.mark.asyncio
async def test_reconciliation_wipe_rewrites_and_reembeds_all_units(
    tmp_path: Path,
) -> None:
    """C1: the obsolete-unit path deletes the file's whole vector
    footprint, so ALL surviving units must get fresh placeholder points
    and re-embeds afterwards."""
    (tmp_path / "m.py").write_text("def f(): return 1\n\ndef g(): return f()\n")
    captured = await _first_run_capture(tmp_path)

    # A ghost row triggers the whole-file wipe; every current unit is
    # otherwise unchanged.
    ghost = AsyncMock(unit_id="ghost-id-not-in-current-batch", source_sha="x")
    existing = [*captured, ghost]
    units_repo2 = _fake_units_repo()
    units_repo2.list_units_for_file = AsyncMock(return_value=existing)
    vector_repo2 = _fake_vector_repo()
    embed_pipe = _RecordingEmbeddingPipeline()
    ctx2 = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c2",
        units_collection="repo_r1",
        units_repo=units_repo2,
        graph_repo=_fake_graph_repo(),
        vector_repo=vector_repo2,
    )
    result = await IngestionPipeline(
        embedding_pipeline=embed_pipe,  # type: ignore[arg-type]
    ).run(ctx2)

    all_ids = {u.unit_id for u in captured}
    vector_repo2.delete_points_for_file.assert_awaited()
    assert _placeholder_point_ids(vector_repo2) == all_ids
    assert _embedded_unit_ids(embed_pipe) == all_ids
    assert result.metrics["units_embedded"] == len(all_ids)


@pytest.mark.asyncio
async def test_reingest_unchanged_without_embedder_keeps_placeholder_writes(
    tmp_path: Path,
) -> None:
    """Without an embedding pipeline every point is a placeholder, so
    the all-units placeholder write stays (harmless, keeps payload
    metadata fresh)."""
    (tmp_path / "m.py").write_text("def f(): return 1\n")
    captured = await _first_run_capture(tmp_path)

    units_repo2 = _fake_units_repo()
    units_repo2.list_units_for_file = AsyncMock(
        side_effect=lambda repo_id, fp: [u for u in captured if u.file_path == fp]
    )
    vector_repo2 = _fake_vector_repo()
    ctx2 = make_context(
        repo_id="r1",
        repo_path=tmp_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo2,
        graph_repo=_fake_graph_repo(),
        vector_repo=vector_repo2,
    )
    result = await IngestionPipeline().run(ctx2)

    assert _placeholder_point_ids(vector_repo2) == {u.unit_id for u in captured}
    assert result.metrics["vector_payloads_written"] == len(captured)


# ---- Bug 1: forward cross-file edges / final edge pass ---------------------
def _make_forward_repo(tmp_path: Path) -> Path:
    """a_caller.py sorts BEFORE z_target.py but points INTO it.

    With per-file edge writes, the CALLS/IMPORTS edges from a_caller were
    sent to the graph store before z_target's nodes existed — the MATCH…
    MERGE matched nothing and the edges were silently dropped (the
    NK-Base forward-relative-import bug). The final edge pass fixes it.
    """
    (tmp_path / "a_caller.py").write_text(
        "import z_target\n\n\ndef caller():\n    return z_target.target_fn()\n"
    )
    (tmp_path / "z_target.py").write_text("def target_fn():\n    return 1\n")
    return tmp_path


def _ordered_graph_repo(events: list[tuple[str, object]]) -> AsyncMock:
    """Graph repo mock that appends every call to a shared event log."""
    repo = AsyncMock()

    async def rec_delete(repo_id, file_path):
        events.append(("delete", file_path))
        return 0

    async def rec_nodes(nodes):
        batch = list(nodes)
        events.append(("nodes", [n.node_id for n in batch]))
        return len(batch)

    async def rec_edges(edges):
        batch = list(edges)
        events.append(("edges", [(e.src_id, e.kind.value, e.dst_id) for e in batch]))
        return len(batch)

    repo.delete_subgraph_for_file = AsyncMock(side_effect=rec_delete)
    repo.upsert_nodes = AsyncMock(side_effect=rec_nodes)
    repo.upsert_edges = AsyncMock(side_effect=rec_edges)
    return repo


@pytest.mark.asyncio
async def test_forward_cross_file_edge_survives_first_ingest(tmp_path: Path) -> None:
    repo_path = _make_forward_repo(tmp_path)
    events: list[tuple[str, object]] = []
    graph_repo = _ordered_graph_repo(events)

    ctx = make_context(
        repo_id="r1",
        repo_path=repo_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=_fake_units_repo(),
        graph_repo=graph_repo,
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline().run(ctx)
    assert result.failed_files == ()

    # Every node upsert happens BEFORE the first edge upsert: edges are
    # written once, run-wide, after all files' nodes exist.
    kinds = [k for k, _ in events]
    assert "edges" in kinds and "nodes" in kinds
    first_edge_call = kinds.index("edges")
    assert all(k != "nodes" for k in kinds[first_edge_call:]), (
        "node upserts ran after the edge pass — edges must come last"
    )
    assert graph_repo.upsert_edges.await_count == 1

    # The forward cross-file CALLS edge resolved to the REAL target unit
    # and survived ingestion.
    caller_id = stable_unit_id("r1", "a_caller.py", "a_caller.caller")
    target_id = stable_unit_id("r1", "z_target.py", "z_target.target_fn")
    all_edges = [e for k, batch in events if k == "edges" for e in batch]  # type: ignore[union-attr]
    assert (caller_id, "CALLS", target_id) in all_edges

    # The forward IMPORTS edge module->module resolved too.
    caller_mod = stable_unit_id("r1", "a_caller.py", "a_caller")
    target_mod = stable_unit_id("r1", "z_target.py", "z_target")
    assert (caller_mod, "IMPORTS", target_mod) in all_edges


@pytest.mark.asyncio
async def test_reingest_wipe_rewrites_inbound_cross_file_edges(tmp_path: Path) -> None:
    """Reconciliation DETACH-DELETEs z_target.py's subgraph, severing the
    inbound CALLS edge from (unchanged) a_caller.py. The final run-wide
    edge pass must restore it — and must run AFTER the wipe."""
    repo_path = _make_forward_repo(tmp_path)
    events: list[tuple[str, object]] = []
    graph_repo = _ordered_graph_repo(events)

    units_repo = _fake_units_repo()
    # A ghost row in z_target.py triggers the whole-file wipe there.
    units_repo.list_units_for_file = AsyncMock(
        side_effect=lambda repo_id, fp: (
            [AsyncMock(unit_id="ghost-id", source_sha="x")]
            if fp == "z_target.py"
            else []
        )
    )

    ctx = make_context(
        repo_id="r1",
        repo_path=repo_path,
        commit_sha="c2",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline().run(ctx)
    assert result.failed_files == ()

    kinds = [k for k, _ in events]
    assert ("delete", "z_target.py") in events
    # The edge pass runs strictly after the wipe.
    assert kinds.index("edges") > kinds.index("delete")

    caller_id = stable_unit_id("r1", "a_caller.py", "a_caller.caller")
    target_id = stable_unit_id("r1", "z_target.py", "z_target.target_fn")
    all_edges = [e for k, batch in events if k == "edges" for e in batch]  # type: ignore[union-attr]
    assert (caller_id, "CALLS", target_id) in all_edges, (
        "inbound cross-file edge severed by the wipe was not rewritten"
    )


@pytest.mark.asyncio
async def test_edges_written_metric_uses_repo_return_value(tmp_path: Path) -> None:
    """`edges_written` must reflect what the graph repo REPORTS as
    written (post-fix: the actual relationship count), not len(edges)."""
    repo_path = _make_forward_repo(tmp_path)
    graph_repo = _fake_graph_repo()

    async def short_count(edges):
        return len(list(edges)) - 1  # pretend one edge was dropped

    graph_repo.upsert_edges = AsyncMock(side_effect=short_count)
    ctx = make_context(
        repo_id="r1",
        repo_path=repo_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=_fake_units_repo(),
        graph_repo=graph_repo,
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline().run(ctx)

    assert graph_repo.upsert_edges.await_count == 1
    [(batch,)] = [c.args for c in graph_repo.upsert_edges.await_args_list]
    assert result.metrics["edges_written"] == len(list(batch)) - 1


@pytest.mark.asyncio
async def test_edge_pass_failure_degrades_but_does_not_fail_ingest(
    tmp_path: Path,
) -> None:
    """A total edge-pass failure must not zero the whole ingest — nodes,
    Postgres rows and vector payloads are already durable."""
    repo_path = _make_forward_repo(tmp_path)
    graph_repo = _fake_graph_repo()
    graph_repo.upsert_edges = AsyncMock(side_effect=RuntimeError("neo4j down"))

    ctx = make_context(
        repo_id="r1",
        repo_path=repo_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=_fake_units_repo(),
        graph_repo=graph_repo,
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline().run(ctx)

    assert result.failed_files == ()
    assert result.metrics["files_failed"] == 0
    assert result.metrics["nodes_written"] > 0
    assert result.metrics["edges_written"] == 0


@pytest.mark.asyncio
async def test_failed_file_does_not_block_final_edge_pass(tmp_path: Path) -> None:
    """If ONE file fails mid-run, the final edge pass still runs for the
    survivors' edges."""
    repo_path = _make_forward_repo(tmp_path)
    units_repo = _fake_units_repo()

    async def fail_for_z(units):
        batch = list(units)
        if any(u.file_path == "z_target.py" for u in batch):
            raise RuntimeError("simulated DB outage")
        return len(batch)

    units_repo.upsert_units = AsyncMock(side_effect=fail_for_z)
    graph_repo = _fake_graph_repo()
    ctx = make_context(
        repo_id="r1",
        repo_path=repo_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline().run(ctx)

    assert result.failed_files == ("z_target.py",)
    # Survivor edges still hit the graph store in the final pass.
    assert graph_repo.upsert_edges.await_count == 1
    [(batch,)] = [c.args for c in graph_repo.upsert_edges.await_args_list]
    srcs = {e.src_id for e in batch}
    caller_mod = stable_unit_id("r1", "a_caller.py", "a_caller")
    assert caller_mod in srcs


# ---- Bug 2: qname collision disambiguation ---------------------------------
async def _run_and_capture_units(tmp_path: Path) -> list[IngestionUnit]:
    captured: list[IngestionUnit] = []
    units_repo = _fake_units_repo()

    async def _capture(units: Iterable[IngestionUnit]) -> int:
        batch = list(units)
        captured.extend(batch)
        return len(batch)

    units_repo.upsert_units = AsyncMock(side_effect=_capture)
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
    assert result.failed_files == ()
    return captured


@pytest.mark.asyncio
async def test_qname_collision_keeps_both_units_with_suffix(tmp_path: Path) -> None:
    """Two same-qname units (overload-style) must BOTH be stored: first
    keeps its qname, second gets `#2` and a recomputed stable unit_id."""
    (tmp_path / "dup.py").write_text(
        "def f():\n    return 1\n\n\ndef f():\n    return 2\n"
    )
    captured = await _run_and_capture_units(tmp_path)

    fns = [u for u in captured if u.kind.value == "fn"]
    assert len(fns) == 2, "second overload was dropped"
    qnames = {u.qualified_name for u in fns}
    assert qnames == {"dup.f", "dup.f#2"}
    ids = {u.unit_id for u in fns}
    assert len(ids) == 2
    suffixed = next(u for u in fns if u.qualified_name == "dup.f#2")
    assert suffixed.unit_id == stable_unit_id("r1", "dup.py", "dup.f#2")
    # First (by line) collider keeps the unsuffixed qname.
    first = next(u for u in fns if u.qualified_name == "dup.f")
    assert first.line_start < suffixed.line_start


@pytest.mark.asyncio
async def test_qname_collision_suffix_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "dup.py").write_text(
        "def f():\n    return 1\n\n\ndef f():\n    return 2\n\n\ndef f():\n    return 3\n"
    )
    run1 = await _run_and_capture_units(tmp_path)
    run2 = await _run_and_capture_units(tmp_path)

    assert [u.unit_id for u in run1] == [u.unit_id for u in run2]
    assert {u.qualified_name for u in run1 if u.kind.value == "fn"} == {
        "dup.f",
        "dup.f#2",
        "dup.f#3",
    }


@pytest.mark.asyncio
async def test_class_qname_collision_remaps_children(tmp_path: Path) -> None:
    """A colliding CLASS gets suffixed and its children's
    parent_qualified_name follows."""
    (tmp_path / "dup.py").write_text(
        "class C:\n"
        "    def m(self):\n"
        "        return 1\n"
        "\n"
        "\n"
        "class C:\n"
        "    def m(self):\n"
        "        return 2\n"
    )
    captured = await _run_and_capture_units(tmp_path)

    classes = [u for u in captured if u.kind.value == "cls"]
    assert {u.qualified_name for u in classes} == {"dup.C", "dup.C#2"}

    methods = [u for u in captured if u.kind.value == "mth"]
    assert {u.qualified_name for u in methods} == {"dup.C.m", "dup.C.m#2"}
    second_method = next(u for u in methods if u.qualified_name == "dup.C.m#2")
    assert second_method.parent_qualified_name == "dup.C#2", (
        "child of the suffixed class must point at the suffixed parent"
    )
    first_method = next(u for u in methods if u.qualified_name == "dup.C.m")
    assert first_method.parent_qualified_name == "dup.C"


def _make_polyglot_repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "tool.py").write_text("def py_fn():\n    return 1\n")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "app.js").write_text(
        'import { score } from "./scorer";\n'
        "export const run = (x) => score(x);\n"
    )
    (tmp_path / "web" / "scorer.ts").write_text(
        "export function score(x: number): number { return x; }\n"
    )
    (tmp_path / "web" / "types.d.ts").write_text("declare const v: number;\n")
    (tmp_path / "web" / "style.css").write_text("body {}\n")
    return tmp_path


@pytest.mark.asyncio
async def test_pipeline_parses_python_and_js_ts(tmp_path: Path) -> None:
    repo_path = _make_polyglot_repo(tmp_path)
    units_repo = _fake_units_repo()
    captured: list = []
    units_repo.upsert_units = AsyncMock(
        side_effect=lambda units: captured.append(list(units)) or len(list(units))
    )

    ctx = make_context(
        repo_id="r1",
        repo_path=repo_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline().run(ctx)

    # 3 parsed files: tool.py, app.js, scorer.ts (.d.ts + .css skipped).
    assert result.metrics["files_parsed"] == 3
    assert result.failed_files == ()

    qnames = {u.qualified_name for batch in captured for u in batch}
    assert "pkg.tool.py_fn" in qnames
    assert "web.app.run" in qnames
    assert "web.scorer.score" in qnames


# ---- Bug 3: collision-proof suffix minting ---------------------------------

def _make_unit(qname: str, line: int) -> IngestionUnit:
    """Minimal IngestionUnit for direct _resolve_qname_collisions tests."""
    src = f"def {qname.split('.')[-1].replace('#', '_')}(): pass"
    return IngestionUnit(
        unit_id=stable_unit_id("r1", "f.py", qname),
        repo_id="r1",
        commit_sha="c1",
        kind=UnitKind.FUNCTION,
        name=qname.split(".")[-1],
        qualified_name=qname,
        file_path="f.py",
        language=Language.PYTHON,
        line_start=line,
        line_end=line + 1,
        content=src,
        source_sha=content_sha(src),
    )


def test_suffix_collision_proof_existing_suffix() -> None:
    """Input [X, X, X#2] must yield [X, X#3, X#2] — the minted suffix must
    skip X#2 because it is already taken by the third unit."""
    units = [
        _make_unit("f.foo", 1),
        _make_unit("f.foo", 3),
        _make_unit("f.foo#2", 5),  # pre-existing X#2 occupies the slot
    ]
    resolved, collisions = _resolve_qname_collisions(units)

    qnames = [u.qualified_name for u in resolved]
    assert qnames[0] == "f.foo"           # first keeps its name
    assert qnames[2] == "f.foo#2"         # pre-existing suffix is untouched
    assert qnames[1] == "f.foo#3"         # minted suffix skipped the occupied slot
    assert len(set(qnames)) == 3, "all three qnames must be distinct"
    # unit_ids must match the (recomputed) qnames
    assert resolved[1].unit_id == stable_unit_id("r1", "f.py", "f.foo#3")
    assert resolved[2].unit_id == stable_unit_id("r1", "f.py", "f.foo#2")
    assert collisions == 1
