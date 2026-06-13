"""Unit tests for the RuntimeConfig -> embedder selector.

Pins the contract that the query side and document side build the SAME
embedder/dimension from a given runtime config (the dimension gotcha
guard). Uses the in-memory fake repo from the config-runtime tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import apps.api.embedding_runtime as er
from apps.api.embedding_runtime import (
    OPENAI_VECTOR_SIZE,
    active_embedding_dimension,
    build_runtime_embedder,
    reindex_repo,
)
from core.config import Settings
from core.config_runtime import RuntimeConfig
from core.embeddings import LocalEmbedder, OpenAIEmbedder
from storage.app_config_repo import AppConfigRow


class _FakeRepo:
    def __init__(self, row: AppConfigRow | None) -> None:
        self._row = row

    async def get(self) -> AppConfigRow | None:
        return self._row


def _row(**kw: object) -> AppConfigRow:
    base: dict[str, object] = {
        "id": 1,
        "mcp_api_key": None,
        "openai_api_key": None,
        "embedding_mode": "openai",
        "embedding_model": None,
        "onboarding_completed": False,
        "updated_at": datetime.now(UTC),
    }
    base.update(kw)
    return AppConfigRow(**base)  # type: ignore[arg-type]


async def _runtime(row: AppConfigRow | None, **settings_kw: object) -> RuntimeConfig:
    rc = RuntimeConfig(_FakeRepo(row), Settings(**settings_kw))  # type: ignore[arg-type]
    await rc.refresh()
    return rc


async def test_disabled_returns_none_and_default_dimension() -> None:
    rc = await _runtime(_row())  # openai mode, no key
    assert build_runtime_embedder(rc) is None
    assert active_embedding_dimension(rc) == OPENAI_VECTOR_SIZE


async def test_openai_mode_builds_openai_embedder_1536() -> None:
    rc = await _runtime(_row(openai_api_key="sk-test"))
    emb = build_runtime_embedder(rc)
    try:
        assert isinstance(emb, OpenAIEmbedder)
        assert emb.dimension == 1536
        assert active_embedding_dimension(rc) == 1536
    finally:
        if emb is not None:
            await emb.aclose()


async def test_local_mode_builds_local_embedder_384() -> None:
    # No OpenAI key — local mode must still build and enable.
    rc = await _runtime(_row(embedding_mode="local"))
    emb = build_runtime_embedder(rc)
    assert isinstance(emb, LocalEmbedder)
    assert emb.dimension == 384
    assert active_embedding_dimension(rc) == 384


async def test_local_dimension_resolves_without_loading_model() -> None:
    rc = await _runtime(_row(embedding_mode="local"))
    # Pure metadata lookup — must not pay the model load just to size a
    # collection.
    assert active_embedding_dimension(rc) == 384


@pytest.mark.parametrize("mode", ["openai", "local"])
async def test_query_and_doc_dimensions_agree(mode: str) -> None:
    # The whole point: both sides derive the same size from one source.
    row = _row(embedding_mode=mode, openai_api_key="sk-test" if mode == "openai" else None)
    rc = await _runtime(row)
    emb = build_runtime_embedder(rc)
    try:
        assert emb is not None
        assert emb.dimension == active_embedding_dimension(rc)
    finally:
        await emb.aclose()  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# reindex_repo
# ---------------------------------------------------------------------------
class _FakeVectorRepo:
    def __init__(self) -> None:
        self.recreated: list[tuple[str, int]] = []
        self.ensured: list[tuple[str, int]] = []

    async def recreate_collection(self, name: str, size: int) -> None:
        self.recreated.append((name, size))

    async def ensure_collection(self, name: str, size: int) -> None:
        self.ensured.append((name, size))


class _FakeUnitsRepo:
    def __init__(self, n: int) -> None:
        self._n = n

    async def list_units_for_repo(self, repo_id: str) -> list[object]:
        return [object() for _ in range(self._n)]


class _FakeState:
    def __init__(self, vr: _FakeVectorRepo, ur: _FakeUnitsRepo) -> None:
        self.vector_repo = vr
        self.units_repo = ur


async def test_reindex_disabled_recreates_collection_but_embeds_nothing() -> None:
    rc = await _runtime(_row())  # openai mode, no key -> embeddings disabled
    vr = _FakeVectorRepo()
    state = _FakeState(vr, _FakeUnitsRepo(5))
    res = await reindex_repo(state, Settings(), rc, "r1", recreate=True)  # type: ignore[arg-type]
    assert vr.recreated == [("repo_r1", OPENAI_VECTOR_SIZE)]
    assert res.units_total == 5
    assert res.units_embedded == 0
    assert res.failed_batches == 0


async def test_reindex_recreate_false_ensures_instead() -> None:
    rc = await _runtime(_row())
    vr = _FakeVectorRepo()
    state = _FakeState(vr, _FakeUnitsRepo(0))
    await reindex_repo(state, Settings(), rc, "r1", recreate=False)  # type: ignore[arg-type]
    assert vr.ensured == [("repo_r1", OPENAI_VECTOR_SIZE)]
    assert vr.recreated == []


async def test_reindex_embeds_in_batches_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_calls: list[int] = []

    class _FakeEmbedder:
        dimension = 384

        async def aclose(self) -> None:
            return None

    class _FakePipeline:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def run(self, batch: list[object], *, collection: str) -> None:
            run_calls.append(len(batch))

    monkeypatch.setattr(er, "build_runtime_embedder", lambda rc: _FakeEmbedder())
    monkeypatch.setattr(er, "EmbeddingPipeline", _FakePipeline)

    rc = await _runtime(_row(embedding_mode="local"))  # enabled, 384-dim
    vr = _FakeVectorRepo()
    # 450 units -> 3 batches at REINDEX_BATCH_SIZE=200 (200, 200, 50).
    state = _FakeState(vr, _FakeUnitsRepo(450))
    res = await reindex_repo(state, Settings(), rc, "r1", recreate=True)  # type: ignore[arg-type]
    assert vr.recreated == [("repo_r1", 384)]
    assert run_calls == [200, 200, 50]
    assert res.units_embedded == 450
    assert res.failed_batches == 0


async def test_reindex_isolates_batch_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeEmbedder:
        dimension = 384

        async def aclose(self) -> None:
            return None

    class _FailingPipeline:
        def __init__(self, **kwargs: object) -> None:
            self._n = 0

        async def run(self, batch: list[object], *, collection: str) -> None:
            self._n += 1
            if self._n == 1:
                raise RuntimeError("provider boom")

    monkeypatch.setattr(er, "build_runtime_embedder", lambda rc: _FakeEmbedder())
    monkeypatch.setattr(er, "EmbeddingPipeline", _FailingPipeline)

    rc = await _runtime(_row(embedding_mode="local"))
    state = _FakeState(_FakeVectorRepo(), _FakeUnitsRepo(300))  # 2 batches
    res = await reindex_repo(state, Settings(), rc, "r1", recreate=True)  # type: ignore[arg-type]
    # First batch fails, second succeeds -> 100 embedded, 1 failed batch.
    assert res.failed_batches == 1
    assert res.units_embedded == 100
