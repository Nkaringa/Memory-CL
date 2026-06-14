"""AppState / lifespan query-embedder wiring (ranking Defect A).

The query-side embedder must live in the same vector space as the
document-side embedder used at ingest. When embeddings are enabled the
query embedder must be an OpenAIEmbedder; otherwise None (the lifespan
then falls back to the deterministic embedder). No OpenAI request is made
here — constructing the embedder only builds an httpx client.

Phase-1 onboarding moved the embedder DECISION onto `RuntimeConfig`
(Postgres-over-env): `_build_query_embedder(runtime)` resolves the key +
mode, and the lifespan upgrades the deterministic placeholder AFTER the
runtime snapshot loads. `_build_state()` itself now always returns the
deterministic fallback (it runs before storage connects), so the tests
exercise the real `_build_query_embedder` seam directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from apps.api.lifespan import _build_query_embedder, _build_state, _close_embedder
from core.config import Settings
from core.config_runtime import RuntimeConfig
from core.embeddings import DeterministicEmbedder, LocalEmbedder, OpenAIEmbedder
from storage.app_config_repo import AppConfigRow


class _FakeAppConfigRepo:
    def __init__(self, row: AppConfigRow | None) -> None:
        self._row = row

    async def get(self) -> AppConfigRow | None:
        return self._row


def _row(**kw: object) -> AppConfigRow:
    base = {
        "id": 1, "mcp_api_key": None, "openai_api_key": None,
        "embedding_mode": "openai", "embedding_model": None,
        "onboarding_completed": False, "updated_at": datetime.now(UTC),
    }
    base.update(kw)
    return AppConfigRow(**base)  # type: ignore[arg-type]


async def _runtime(row: AppConfigRow | None, settings: Settings) -> RuntimeConfig:
    rc = RuntimeConfig(_FakeAppConfigRepo(row), settings)  # type: ignore[arg-type]
    await rc.refresh()
    return rc


async def test_query_embedder_is_openai_when_embeddings_enabled() -> None:
    rc = await _runtime(_row(openai_api_key="sk-test-not-a-real-key"), Settings())
    embedder = _build_query_embedder(rc)
    assert isinstance(embedder, OpenAIEmbedder)
    # Must match the ingest-side collection dimension.
    assert embedder.dimension == 1536
    await embedder.aclose()


async def test_query_embedder_none_when_disabled() -> None:
    rc = await _runtime(_row(), Settings(openai_api_key=None))
    assert _build_query_embedder(rc) is None


async def test_query_embedder_is_local_for_local_mode() -> None:
    """'local' mode builds the on-device embedder (384-dim) regardless of
    whether an OpenAI key is present — it needs no key."""
    rc = await _runtime(_row(embedding_mode="local"), Settings(openai_api_key=None))
    embedder = _build_query_embedder(rc)
    assert isinstance(embedder, LocalEmbedder)
    assert embedder.dimension == 384


def test_build_state_returns_deterministic_placeholder_embedder() -> None:
    """`_build_state` runs before storage connects, so it always wires the
    deterministic fallback; the lifespan upgrades it post-refresh."""
    state, _cfg_repo, _registry, _runtime_cfg, _token_cache = _build_state()
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
