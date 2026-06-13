from __future__ import annotations

import asyncio
import textwrap
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routers import ingest as ingest_router
from apps.api.state import AppState


def _build_app(state: AppState) -> FastAPI:
    @asynccontextmanager
    async def _ls(app: FastAPI):
        app.state.app_state = state
        yield

    app = FastAPI(lifespan=_ls)
    app.include_router(ingest_router.router)
    return app


def _make_state(*, vector_repo_extra: AsyncMock | None = None) -> AppState:
    units_repo = AsyncMock()
    units_repo.list_units_for_file = AsyncMock(return_value=[])
    units_repo.delete_units_for_file = AsyncMock(return_value=0)
    units_repo.upsert_units = AsyncMock(side_effect=lambda u: len(list(u)))
    units_repo.ensure_schema = AsyncMock()

    graph_repo = AsyncMock()
    graph_repo.delete_subgraph_for_file = AsyncMock(return_value=0)
    graph_repo.upsert_nodes = AsyncMock(side_effect=lambda n: len(list(n)))
    graph_repo.upsert_edges = AsyncMock(side_effect=lambda e: len(list(e)))
    graph_repo.ensure_constraints = AsyncMock()

    vector_repo = vector_repo_extra or AsyncMock()
    vector_repo.delete_points_for_file = AsyncMock(return_value=0)
    vector_repo.upsert_payloads = AsyncMock(side_effect=lambda c, p: len(list(p)))
    vector_repo.ensure_collection = AsyncMock()

    return AppState.with_default_embedder(
        postgres=AsyncMock(),
        qdrant=AsyncMock(),
        neo4j=AsyncMock(),
        redis=AsyncMock(),
        units_repo=units_repo,
        graph_repo=graph_repo,
        vector_repo=vector_repo,
        embedding_dimension=32,
    )


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "m.py").write_text(textwrap.dedent("""
        def f(): return 1
        def g(): return f()
    """).lstrip())
    return tmp_path


def test_ingest_endpoint_runs_pipeline(tmp_path: Path) -> None:
    state = _make_state()
    app = _build_app(state)
    repo_path = _make_repo(tmp_path)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "tenant-1",
                "repo_path": str(repo_path),
                "commit_sha": "abc123",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["repo_id"] == "tenant-1"
    assert body["commit_sha"] == "abc123"
    assert body["units_collection"] == "repo_tenant-1"
    assert body["metrics"]["files_walked"] >= 1
    assert body["metrics"]["units_emitted"] >= 1
    assert body["failed_files"] == []

    # ensure_collection was called by the endpoint with the per-repo name.
    state.vector_repo.ensure_collection.assert_awaited_once()
    name_arg = state.vector_repo.ensure_collection.await_args.args[0]
    assert name_arg == "repo_tenant-1"


def test_ingest_rejects_bad_repo_path(tmp_path: Path) -> None:
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "r",
                "repo_path": str(tmp_path / "does-not-exist"),
                "commit_sha": "c",
            },
        )

    assert resp.status_code == 400
    assert "not a directory" in resp.json()["detail"]


def test_ingest_request_validation() -> None:
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        # Missing required field.
        resp = client.post("/ingest", json={"repo_id": "r"})
        assert resp.status_code == 422

        # Extra field rejected (extra="forbid" on the request schema).
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "r",
                "repo_path": "/tmp",
                "commit_sha": "c",
                "rogue_field": "x",
            },
        )
        assert resp.status_code == 422


# ---- Phase 3: embedding wiring + reembed backfill ---------------------------
class _RecordingEmbeddingPipeline:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[tuple[list[Any], str]] = []
        self._fail_on_call = fail_on_call

    async def run(self, units: Sequence[Any], *, collection: str) -> None:
        self.calls.append((list(units), collection))
        if self._fail_on_call == len(self.calls):
            raise RuntimeError("simulated provider outage")


def test_ingest_embeds_units_when_embeddings_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    fake_pipe = _RecordingEmbeddingPipeline()
    fake_embedder = AsyncMock()
    monkeypatch.setattr(
        ingest_router,
        "_build_embedding_components",
        lambda state, settings, runtime=None: (fake_pipe, fake_embedder),
    )
    state = _make_state()
    app = _build_app(state)
    repo_path = _make_repo(tmp_path)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "tenant-1",
                "repo_path": str(repo_path),
                "commit_sha": "abc123",
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    # Fresh repo: every emitted unit got embedded.
    assert body["metrics"]["units_embedded"] == body["metrics"]["units_emitted"]
    assert fake_pipe.calls
    assert all(coll == "repo_tenant-1" for _, coll in fake_pipe.calls)
    # The embedder's HTTP client is closed after the request.
    fake_embedder.aclose.assert_awaited_once()


def test_ingest_reports_zero_embedded_when_embeddings_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _make_state()
    app = _build_app(state)
    repo_path = _make_repo(tmp_path)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "tenant-1",
                "repo_path": str(repo_path),
                "commit_sha": "abc123",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["metrics"]["units_embedded"] == 0


def test_reembed_rejected_when_embeddings_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post("/ingest/reembed", json={"repo_id": "tenant-1"})

    assert resp.status_code == 400
    assert "OPENAI_API_KEY" in resp.json()["detail"]
    cast(AsyncMock, state.units_repo.list_units_for_repo).assert_not_awaited()


def test_reembed_happy_path_batches_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    fake_pipe = _RecordingEmbeddingPipeline()
    fake_embedder = AsyncMock()
    monkeypatch.setattr(
        ingest_router,
        "_build_embedding_components",
        lambda state, settings, runtime=None: (fake_pipe, fake_embedder),
    )
    monkeypatch.setattr(ingest_router, "_REEMBED_BATCH_SIZE", 2)

    state = _make_state()
    units = [object() for _ in range(5)]
    state.units_repo.list_units_for_repo = AsyncMock(  # type: ignore[method-assign]
        return_value=units
    )
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post("/ingest/reembed", json={"repo_id": "tenant-1"})

    assert resp.status_code == 200
    assert resp.json() == {
        "repo_id": "tenant-1",
        "units_total": 5,
        "units_embedded": 5,
        "failed_batches": 0,
    }
    # 5 units at batch size 2 → 3 batches against the repo collection.
    assert [len(batch) for batch, _ in fake_pipe.calls] == [2, 2, 1]
    assert all(coll == "repo_tenant-1" for _, coll in fake_pipe.calls)
    fake_embedder.aclose.assert_awaited_once()


def test_reembed_continues_past_batch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    fake_pipe = _RecordingEmbeddingPipeline(fail_on_call=2)
    fake_embedder = AsyncMock()
    monkeypatch.setattr(
        ingest_router,
        "_build_embedding_components",
        lambda state, settings, runtime=None: (fake_pipe, fake_embedder),
    )
    monkeypatch.setattr(ingest_router, "_REEMBED_BATCH_SIZE", 2)

    state = _make_state()
    state.units_repo.list_units_for_repo = AsyncMock(  # type: ignore[method-assign]
        return_value=[object() for _ in range(5)]
    )
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post("/ingest/reembed", json={"repo_id": "tenant-1"})

    assert resp.status_code == 200
    # Batch 2 (2 units) failed; batches 1 and 3 (2 + 1 units) succeeded.
    assert resp.json() == {
        "repo_id": "tenant-1",
        "units_total": 5,
        "units_embedded": 3,
        "failed_batches": 1,
    }
    assert len(fake_pipe.calls) == 3
    fake_embedder.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_reembed_concurrent_same_repo_returns_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I3: a reembed for a repo that already has one in flight is
    rejected with 409 — it would double-spend on the provider and race
    the same Qdrant points. The guard is released afterwards."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    started = asyncio.Event()
    gate = asyncio.Event()

    class _BlockingPipeline:
        async def run(self, units: Sequence[Any], *, collection: str) -> None:
            started.set()
            await gate.wait()

    fake_embedder = AsyncMock()
    monkeypatch.setattr(
        ingest_router,
        "_build_embedding_components",
        lambda state, settings, runtime=None: (_BlockingPipeline(), fake_embedder),
    )
    state = _make_state()
    state.units_repo.list_units_for_repo = AsyncMock(  # type: ignore[method-assign]
        return_value=[object()]
    )
    app = _build_app(state)
    app.state.app_state = state  # ASGITransport does not run lifespan

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        first = asyncio.create_task(
            client.post("/ingest/reembed", json={"repo_id": "tenant-1"})
        )
        await started.wait()
        # Same repo while in flight → 409 without touching the provider.
        # (wait_for so a missing guard fails the test instead of
        # deadlocking it on the blocked pipeline.)
        second = await asyncio.wait_for(
            client.post("/ingest/reembed", json={"repo_id": "tenant-1"}),
            timeout=5.0,
        )
        assert second.status_code == 409
        assert "in progress" in second.json()["detail"]

        gate.set()
        resp1 = await first
        assert resp1.status_code == 200

        # Guard released after completion — a follow-up run succeeds.
        third = await client.post("/ingest/reembed", json={"repo_id": "tenant-1"})
        assert third.status_code == 200


# ---- auth (mirrors tests/test_mcp_router.py) -------------------------------
# The ingest endpoints are mutations (reembed even spends provider money),
# so they sit behind the same ApiKeyDep as POST /mcp/tools/{name}:
# key configured → required; key unset → dev mode, keyless allowed.


def test_ingest_rejects_request_without_api_key_when_key_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "secret-123")
    state = _make_state()
    app = _build_app(state)
    repo_path = _make_repo(tmp_path)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "tenant-1",
                "repo_path": str(repo_path),
                "commit_sha": "abc123",
            },
        )

    assert resp.status_code == 401
    # Auth short-circuits before any pipeline work.
    state.vector_repo.ensure_collection.assert_not_awaited()


def test_ingest_accepts_correct_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "secret-123")
    state = _make_state()
    app = _build_app(state)
    repo_path = _make_repo(tmp_path)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "tenant-1",
                "repo_path": str(repo_path),
                "commit_sha": "abc123",
            },
            headers={"X-API-Key": "secret-123"},
        )

    assert resp.status_code == 200
    assert resp.json()["repo_id"] == "tenant-1"


def test_ingest_allows_keyless_request_in_dev_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev mode: mcp_api_key not set → keyless ingest allowed."""
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    state = _make_state()
    app = _build_app(state)
    repo_path = _make_repo(tmp_path)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "tenant-1",
                "repo_path": str(repo_path),
                "commit_sha": "abc123",
            },
        )

    assert resp.status_code == 200


def test_reembed_rejects_request_without_api_key_when_key_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "secret-123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post("/ingest/reembed", json={"repo_id": "tenant-1"})

    assert resp.status_code == 401
    # Auth short-circuits before any provider spend.
    cast(AsyncMock, state.units_repo.list_units_for_repo).assert_not_awaited()


def test_reembed_accepts_correct_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_API_KEY", "secret-123")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    fake_pipe = _RecordingEmbeddingPipeline()
    fake_embedder = AsyncMock()
    monkeypatch.setattr(
        ingest_router,
        "_build_embedding_components",
        lambda state, settings, runtime=None: (fake_pipe, fake_embedder),
    )
    state = _make_state()
    state.units_repo.list_units_for_repo = AsyncMock(  # type: ignore[method-assign]
        return_value=[object()]
    )
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest/reembed",
            json={"repo_id": "tenant-1"},
            headers={"X-API-Key": "secret-123"},
        )

    assert resp.status_code == 200
    assert resp.json()["units_embedded"] == 1


def test_reembed_allows_keyless_request_in_dev_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev mode: mcp_api_key not set → keyless reembed allowed."""
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    fake_pipe = _RecordingEmbeddingPipeline()
    fake_embedder = AsyncMock()
    monkeypatch.setattr(
        ingest_router,
        "_build_embedding_components",
        lambda state, settings, runtime=None: (fake_pipe, fake_embedder),
    )
    state = _make_state()
    state.units_repo.list_units_for_repo = AsyncMock(  # type: ignore[method-assign]
        return_value=[object()]
    )
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post("/ingest/reembed", json={"repo_id": "tenant-1"})

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_ingest_endpoint_propagates_failed_files(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("def f(): pass\n")
    (tmp_path / "bad.py").write_text("def broken(:\n")
    state = _make_state()
    app = _build_app(state)

    with TestClient(app) as client:
        resp = client.post(
            "/ingest",
            json={
                "repo_id": "t",
                "repo_path": str(tmp_path),
                "commit_sha": "c",
            },
        )

    assert resp.status_code == 200
    assert "bad.py" in resp.json()["failed_files"]
