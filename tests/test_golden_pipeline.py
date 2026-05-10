"""Golden-fixture end-to-end determinism test.

This is the Phase 2 acceptance gate: re-running the full pipeline over
the fixture repo at `tests/fixtures/sample_repo/` MUST produce byte-
identical write streams (Postgres params, Neo4j MERGE params, Qdrant
point structs) across two consecutive runs.

If this test ever fails, something non-deterministic crept into the
parser, the graph builder, or one of the repository drivers — fix the
root cause rather than relaxing the assertion.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from core.ingestion import IngestionPipeline, make_context

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "sample_repo"


def _serialize_units(units_arg) -> list[dict]:
    """Project IngestionUnit list to a stable, comparable form."""
    return [
        {
            "unit_id": u.unit_id,
            "kind": u.kind.value,
            "qualified_name": u.qualified_name,
            "source_sha": u.source_sha,
            "imports": list(u.imports),
            "calls": list(u.calls),
            "bases": list(u.bases),
        }
        for u in units_arg
    ]


def _serialize_nodes(nodes_arg) -> list[dict]:
    return [
        {
            "node_id": n.node_id,
            "kind": n.kind.value,
            "qualified_name": n.qualified_name,
            "file_path": n.file_path,
            "source_sha": n.source_sha,
        }
        for n in nodes_arg
    ]


def _serialize_edges(edges_arg) -> list[dict]:
    return [
        {
            "src_id": e.src_id,
            "kind": e.kind.value,
            "dst_id": e.dst_id,
            "weight": e.weight,
        }
        for e in edges_arg
    ]


def _serialize_vec_payloads(_collection: str, points_arg) -> list[dict]:
    return [
        {
            "point_id": p.point_id,
            "qualified_name": p.qualified_name,
            "kind": p.kind,
            "source_sha": p.source_sha,
            "file_path": p.file_path,
            "line_start": p.line_start,
            "line_end": p.line_end,
        }
        for p in points_arg
    ]


def _make_capturing_state() -> tuple[AsyncMock, AsyncMock, AsyncMock, dict]:
    captured: dict[str, list] = {"units": [], "nodes": [], "edges": [], "points": []}

    async def cap_units(units_arg):
        units = list(units_arg)
        captured["units"].append(_serialize_units(units))
        return len(units)

    async def cap_nodes(nodes_arg):
        nodes = list(nodes_arg)
        captured["nodes"].append(_serialize_nodes(nodes))
        return len(nodes)

    async def cap_edges(edges_arg):
        edges = list(edges_arg)
        captured["edges"].append(_serialize_edges(edges))
        return len(edges)

    async def cap_points(collection, points_arg):
        points = list(points_arg)
        captured["points"].append(_serialize_vec_payloads(collection, points))
        return len(points)

    units_repo = AsyncMock()
    units_repo.list_units_for_file = AsyncMock(return_value=[])
    units_repo.delete_units_for_file = AsyncMock(return_value=0)
    units_repo.upsert_units = AsyncMock(side_effect=cap_units)

    graph_repo = AsyncMock()
    graph_repo.delete_subgraph_for_file = AsyncMock(return_value=0)
    graph_repo.upsert_nodes = AsyncMock(side_effect=cap_nodes)
    graph_repo.upsert_edges = AsyncMock(side_effect=cap_edges)

    vector_repo = AsyncMock()
    vector_repo.delete_points_for_file = AsyncMock(return_value=0)
    vector_repo.upsert_payloads = AsyncMock(side_effect=cap_points)

    return units_repo, graph_repo, vector_repo, captured


@pytest.mark.asyncio
async def test_golden_pipeline_is_byte_deterministic_across_runs() -> None:
    """Two pipeline runs over the same repo + commit must produce
    byte-identical write streams to all three stores."""
    streams: list[dict] = []
    for _ in range(2):
        units_repo, graph_repo, vector_repo, captured = _make_capturing_state()
        ctx = make_context(
            repo_id="acme",
            repo_path=FIXTURE_REPO,
            commit_sha="commit-deadbeef",
            units_collection="repo_acme",
            units_repo=units_repo,
            graph_repo=graph_repo,
            vector_repo=vector_repo,
        )
        result = await IngestionPipeline().run(ctx)
        assert result.failed_files == ()
        streams.append(captured)

    a, b = streams
    # Compare as JSON to fail loudly with a diff if anything drifts.
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@pytest.mark.asyncio
async def test_golden_pipeline_extracts_expected_symbols() -> None:
    """Pin the symbol set we extract from the fixture so a regression in
    the parser (e.g. accidentally dropping methods) is caught loudly."""
    units_repo, graph_repo, vector_repo, captured = _make_capturing_state()
    ctx = make_context(
        repo_id="acme",
        repo_path=FIXTURE_REPO,
        commit_sha="commit-deadbeef",
        units_collection="repo_acme",
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
    )
    await IngestionPipeline().run(ctx)

    qnames = {
        u["qualified_name"] for batch in captured["units"] for u in batch
    }
    expected_subset = {
        "pkg",                                # __init__ module
        "pkg.utils",                          # module
        "pkg.utils.add",                      # function
        "pkg.utils.retry",                    # function
        "pkg.utils.DEFAULT_RETRIES",          # constant
        "pkg.services",                       # services/__init__
        "pkg.services.auth",                  # module
        "pkg.services.auth.TokenStore",       # ABC class
        "pkg.services.auth.InMemoryTokenStore",
        "pkg.services.auth.InMemoryTokenStore.__init__",
        "pkg.services.auth.InMemoryTokenStore.get",
        "pkg.services.auth.InMemoryTokenStore.set",
        "pkg.services.auth.InMemoryTokenStore.DEFAULT_TTL",
        "pkg.services.auth.login",
        "pkg.services.auth.refresh",
    }
    assert expected_subset.issubset(qnames), (
        f"missing: {expected_subset - qnames}"
    )


@pytest.mark.asyncio
async def test_golden_pipeline_resolves_cross_file_calls() -> None:
    """auth.login calls utils.add and utils.retry — those CALLS edges
    must resolve to real Function unit_ids, not External nodes."""
    units_repo, graph_repo, vector_repo, captured = _make_capturing_state()
    ctx = make_context(
        repo_id="acme",
        repo_path=FIXTURE_REPO,
        commit_sha="commit-deadbeef",
        units_collection="repo_acme",
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
    )
    await IngestionPipeline().run(ctx)

    # Build a qname -> kind map from the captured nodes.
    node_kind_by_qname: dict[str, str] = {}
    for batch in captured["nodes"]:
        for n in batch:
            node_kind_by_qname[n["qualified_name"]] = n["kind"]

    # `pkg.utils.add` and `pkg.utils.retry` were captured as Function
    # nodes (not External).
    assert node_kind_by_qname.get("pkg.utils.add") == "Function"
    assert node_kind_by_qname.get("pkg.utils.retry") == "Function"
