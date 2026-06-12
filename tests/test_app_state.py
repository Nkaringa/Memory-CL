"""AppState / lifespan query-embedder wiring (ranking Defect A).

The query-side embedder must live in the same vector space as the
document-side embedder used at ingest. When embeddings are enabled
(OPENAI_API_KEY configured) the AppState must carry an OpenAIEmbedder;
otherwise the deterministic fallback. No OpenAI request is made here —
constructing the embedder only builds an httpx client.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from apps.api.lifespan import _build_state, _close_embedder
from core.embeddings import DeterministicEmbedder, OpenAIEmbedder


async def test_build_state_wires_openai_embedder_when_embeddings_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-key")
    state = _build_state()
    try:
        assert isinstance(state.embedder, OpenAIEmbedder)
        # Must match the ingest-side collection dimension.
        assert state.embedder.dimension == 1536
    finally:
        await state.embedder.aclose()


def test_build_state_falls_back_to_deterministic_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = _build_state()
    assert isinstance(state.embedder, DeterministicEmbedder)


async def test_close_embedder_calls_aclose_when_present() -> None:
    embedder = AsyncMock()
    await _close_embedder(embedder)
    embedder.aclose.assert_awaited_once()


async def test_close_embedder_tolerates_missing_aclose() -> None:
    # DeterministicEmbedder has no aclose — must be a no-op, not an error.
    await _close_embedder(DeterministicEmbedder(dimension=8))


async def test_close_embedder_swallows_close_errors() -> None:
    failing = AsyncMock()
    failing.aclose = AsyncMock(side_effect=RuntimeError("already closed"))
    await _close_embedder(failing)  # must not raise
