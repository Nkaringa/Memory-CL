"""Unit tests for the RuntimeConfig -> embedder selector.

Pins the contract that the query side and document side build the SAME
embedder/dimension from a given runtime config (the dimension gotcha
guard). Uses the in-memory fake repo from the config-runtime tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.api.embedding_runtime import (
    OPENAI_VECTOR_SIZE,
    active_embedding_dimension,
    build_runtime_embedder,
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
