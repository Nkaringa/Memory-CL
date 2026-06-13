"""Runtime configuration + key-management surface (onboarding Phase 1).

Lets an operator generate/rotate the MCP key, set/clear the OpenAI key,
and choose the embedding mode self-serve — applied WITHOUT a restart.
Every write goes through `AppConfigRepository` and is immediately
followed by `RuntimeConfig.refresh()` so the new value is live for the
next request (auth + embedder both read the same snapshot).

SECURITY
--------
* GET /config NEVER returns a raw key — only a masked hint + booleans.
* Generate / rotate return the new key ONCE in the response body; it is
  never persisted in a recoverable-to-the-client form again.
* Bootstrap auth rule (chicken-and-egg): when NO mcp key is configured,
  the setup endpoints are OPEN (the API is already keyless-open in that
  state) so the wizard can mint the first key. Once a key IS configured,
  the mutating endpoints REQUIRE it — nobody rotates a live system's key
  anonymously. `rotate` ALWAYS requires the key (it only makes sense on a
  configured system).
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.dependencies import AppStateDep, RuntimeConfigDep
from apps.api.embedding_runtime import reindex_repo
from apps.mcp.auth import ApiKeyDep
from core import get_logger, get_settings

router = APIRouter(prefix="/config", tags=["config"])

_log = get_logger(__name__)

# Length of generated MCP keys. token_urlsafe(32) → ~43 url-safe chars.
_MCP_KEY_ENTROPY_BYTES = 32


# ---------------------------------------------------------------------------
# Response / request models
# ---------------------------------------------------------------------------
class ConfigStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configured: bool
    onboarding_completed: bool
    embedding_mode: str
    embeddings_enabled: bool
    has_openai_key: bool
    mcp_key_hint: str | None


class GeneratedKeyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str


class OpenAiKeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # None / "" clears the runtime OpenAI key (falls back to env, if any).
    api_key: str | None = Field(default=None)


class EmbeddingModeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(description="'openai' | 'local'")


class OkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool = True


class EmbeddingModeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool = True
    mode: str
    # True when the mode actually changed and collections were rebuilt at
    # the new dimension. False when the requested mode equalled the current
    # one (idempotent no-op — no re-index).
    reindexed: bool
    repos_reindexed: int = 0
    units_embedded: int = 0
    failed_batches: int = 0


# ---------------------------------------------------------------------------
# Bootstrap auth helper
# ---------------------------------------------------------------------------
def _require_bootstrap_or_authed(runtime: RuntimeConfigDep, api_key: str | None) -> None:
    """Enforce the bootstrap rule: open when unconfigured, else require key.

    `api_key` is the result of `ApiKeyDep`. When the system is NOT yet
    configured, `ApiKeyDep` already short-circuits to dev-mode (returns
    None) so any caller is allowed — we let it through. Once configured,
    `ApiKeyDep` has ALREADY enforced the key (raising 401 on miss/wrong),
    so reaching here means the caller is authenticated. This helper exists
    to make the rule explicit and to guard the edge case where the key was
    configured between dependency resolution and handler entry.
    """
    if runtime.configured() and api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required — this instance is already configured",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("", response_model=ConfigStateResponse)
async def get_config(runtime: RuntimeConfigDep) -> ConfigStateResponse:
    """Onboarding/runtime state. Unauthenticated — the wizard needs it
    BEFORE any key exists. NEVER returns raw keys (only a masked hint)."""
    return ConfigStateResponse(
        configured=runtime.configured(),
        onboarding_completed=runtime.onboarding_completed(),
        embedding_mode=runtime.embedding_mode(),
        embeddings_enabled=runtime.embeddings_enabled(),
        has_openai_key=runtime.openai_api_key() is not None,
        mcp_key_hint=runtime.mcp_key_hint(),
    )


@router.post("/mcp-key/generate", response_model=GeneratedKeyResponse)
async def generate_mcp_key(
    runtime: RuntimeConfigDep,
    api_key: ApiKeyDep,
) -> GeneratedKeyResponse:
    """Mint the first MCP key (or replace via the bootstrap rule).

    Bootstrap: allowed WITHOUT auth only while unconfigured. Once a key
    exists, requires the current key (ApiKeyDep enforced it upstream;
    `_require_bootstrap_or_authed` makes the rule explicit). Returns the
    new key ONCE — the only chance to copy it."""
    _require_bootstrap_or_authed(runtime, api_key)
    new_key = secrets.token_urlsafe(_MCP_KEY_ENTROPY_BYTES)
    await runtime.repo.set_mcp_api_key(new_key)
    await runtime.refresh()
    return GeneratedKeyResponse(api_key=new_key)


@router.post("/mcp-key/rotate", response_model=GeneratedKeyResponse)
async def rotate_mcp_key(
    runtime: RuntimeConfigDep,
    api_key: ApiKeyDep,
) -> GeneratedKeyResponse:
    """Rotate the MCP key. ALWAYS requires the current key — rotation only
    makes sense on a configured system, and an anonymous rotate would be a
    lockout vector. Agents must re-add the new key after this."""
    if not runtime.configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no key configured yet — use /config/mcp-key/generate",
        )
    if api_key is None:
        # Defensive: ApiKeyDep should already have enforced this when
        # configured. Belt-and-suspenders so a wiring change can't open it.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required to rotate",
            headers={"WWW-Authenticate": "Bearer"},
        )
    new_key = secrets.token_urlsafe(_MCP_KEY_ENTROPY_BYTES)
    await runtime.repo.set_mcp_api_key(new_key)
    await runtime.refresh()
    return GeneratedKeyResponse(api_key=new_key)


@router.post("/openai-key", response_model=OkResponse)
async def set_openai_key(
    body: OpenAiKeyRequest,
    runtime: RuntimeConfigDep,
    api_key: ApiKeyDep,
) -> OkResponse:
    """Set or clear the runtime OpenAI key. Bootstrap-or-authed.

    A non-null key must look like an OpenAI secret (`sk-...`). Passing
    null/empty clears the runtime override (falling back to the env key,
    if any). Invalidates the RuntimeConfig cache so embeddings flip on the
    next ingest without a restart."""
    _require_bootstrap_or_authed(runtime, api_key)
    key = body.api_key
    if key is not None and key.strip():
        cleaned = key.strip()
        if not cleaned.startswith("sk-"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OpenAI API key must start with 'sk-'",
            )
        await runtime.repo.set_openai_api_key(cleaned)
    else:
        await runtime.repo.set_openai_api_key(None)
    await runtime.refresh()
    return OkResponse()


@router.post("/embedding-mode", response_model=EmbeddingModeResponse)
async def set_embedding_mode(
    body: EmbeddingModeRequest,
    runtime: RuntimeConfigDep,
    state: AppStateDep,
    api_key: ApiKeyDep,
) -> EmbeddingModeResponse:
    """Choose the embedding mode ('openai' | 'local'). Bootstrap-or-authed.

    Switching modes changes the vector dimension (openai 1536 ↔ local 384),
    so the existing per-repo Qdrant collections — sized for the old model —
    become incompatible. On an ACTUAL change this rebuilds every repo's
    collection at the new dimension and re-embeds its stored units with the
    new model, so retrieval keeps working (rather than silently returning
    noise against mismatched vectors). A no-op request (mode unchanged)
    skips the re-index. The re-index runs inline; for a self-hosted corpus
    this is seconds-to-minutes — acceptable for an explicit operator action.
    """
    _require_bootstrap_or_authed(runtime, api_key)
    if body.mode not in ("openai", "local"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="mode must be 'openai' or 'local'",
        )

    previous_mode = runtime.embedding_mode()
    await runtime.repo.set_embedding_mode(body.mode)
    await runtime.refresh()

    if body.mode == previous_mode:
        return EmbeddingModeResponse(mode=body.mode, reindexed=False)

    _log.info(
        "embedding_mode_changed",
        previous=previous_mode,
        new=body.mode,
    )
    settings = get_settings()
    repos = await state.units_repo.list_repos()
    repos_reindexed = 0
    units_embedded = 0
    failed_batches = 0
    for summary in repos:
        result = await reindex_repo(
            state, settings, runtime, summary.repo_id, recreate=True
        )
        repos_reindexed += 1
        units_embedded += result.units_embedded
        failed_batches += result.failed_batches
    _log.info(
        "embedding_mode_reindex_complete",
        mode=body.mode,
        repos=repos_reindexed,
        units_embedded=units_embedded,
        failed_batches=failed_batches,
    )
    return EmbeddingModeResponse(
        mode=body.mode,
        reindexed=True,
        repos_reindexed=repos_reindexed,
        units_embedded=units_embedded,
        failed_batches=failed_batches,
    )


@router.post("/complete-onboarding", response_model=OkResponse)
async def complete_onboarding(
    runtime: RuntimeConfigDep,
    api_key: ApiKeyDep,
) -> OkResponse:
    """Mark the first-run wizard done. Bootstrap-or-authed."""
    _require_bootstrap_or_authed(runtime, api_key)
    await runtime.repo.set_onboarding_completed(True)
    await runtime.refresh()
    return OkResponse()
