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
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from apps.api.auth_deps import SoftPrincipalDep
from apps.api.dependencies import AppStateDep, AuthProviderRepoDep, OAuthRegistryDep, RuntimeConfigDep, TokenCacheDep
from apps.api.embedding_runtime import reindex_repo
from apps.mcp.auth import _extract_api_key
from core import get_logger, get_settings
from core.auth import Principal
from core.auth.providers import normalize_provider_type
from schemas.auth_providers import EnableRequest, ProviderCreate, ProviderListResponse, ProviderUpdate, ProviderView

router = APIRouter(prefix="/config", tags=["config"])

_log = get_logger(__name__)

# Length of generated MCP keys. token_urlsafe(32) → ~43 url-safe chars.
_MCP_KEY_ENTROPY_BYTES = 32


# ---------------------------------------------------------------------------
# Soft API-key resolver (config-surface only)
# ---------------------------------------------------------------------------
def _resolve_api_key_soft(request: Request) -> str | None:
    """Extract and validate the presented API key WITHOUT raising 401.

    Unlike `require_mcp_api_key` (ApiKeyDep), this dependency returns None
    when no key is presented or the key is wrong — it never raises.
    The config gate (`_require_bootstrap_or_authed`) then combines this
    with the session-based principal to decide whether to allow or reject.

    This decoupling lets session-authenticated users reach the route handler
    even when no API key is in the request headers.
    """
    from apps.mcp.token_auth import auth_is_configured, credential_accepted

    x_api_key_hdr = request.headers.get("X-API-Key")
    authorization_hdr = request.headers.get("Authorization")
    presented = _extract_api_key(x_api_key_hdr, authorization_hdr)
    if presented is None:
        return None

    runtime = getattr(request.app.state, "runtime_config", None)
    if runtime is not None:
        from apps.mcp.auth import _resolve_expected_key
        expected = _resolve_expected_key(request)
    else:
        from core import get_settings as _gs
        s = _gs()
        k = s.mcp_api_key
        expected = k.get_secret_value() if (k and k.get_secret_value().strip()) else None

    token_cache = getattr(request.app.state, "token_cache", None)
    if not auth_is_configured(expected, token_cache):
        return presented  # dev mode — any presented key is accepted

    return presented if credential_accepted(presented, expected, token_cache) else None


_SoftApiKeyDep = Annotated[str | None, Depends(_resolve_api_key_soft)]


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
    has_webhook_secret: bool
    mcp_key_hint: str | None


class GeneratedKeyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str


class WebhookSecretResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret: str


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
def _require_bootstrap_or_authed(
    runtime: RuntimeConfigDep,
    api_key: str | None,
    principal: Principal | None = None,
) -> None:
    """Enforce the bootstrap rule: open when unconfigured, else require auth.

    Passes when ANY of the following is true:
    - The system is NOT yet configured (bootstrap window — open to all).
    - A valid API key was presented (`api_key is not None`).
    - An authenticated human session is active
      (`principal is not None and principal.is_authenticated`).

    Phase-1 AUTHENTICATION: any authenticated principal (user or agent)
    satisfies the gate. Fine-grained per-repo RBAC is a later phase.
    """
    if runtime.configured():
        has_key = api_key is not None
        has_session = principal is not None and principal.is_authenticated
        if not has_key and not has_session:
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
        has_webhook_secret=runtime.webhook_secret() is not None,
        mcp_key_hint=runtime.mcp_key_hint(),
    )


@router.post("/mcp-key/generate", response_model=GeneratedKeyResponse)
async def generate_mcp_key(
    runtime: RuntimeConfigDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
) -> GeneratedKeyResponse:
    """Mint the first MCP key (or replace via the bootstrap rule).

    Bootstrap: allowed WITHOUT auth only while unconfigured. Once a key
    exists, requires the current key or an authenticated session.
    Returns the new key ONCE — the only chance to copy it."""
    _require_bootstrap_or_authed(runtime, api_key, principal)
    new_key = secrets.token_urlsafe(_MCP_KEY_ENTROPY_BYTES)
    await runtime.repo.set_mcp_api_key(new_key)
    await runtime.refresh()
    return GeneratedKeyResponse(api_key=new_key)


@router.post("/mcp-key/rotate", response_model=GeneratedKeyResponse)
async def rotate_mcp_key(
    runtime: RuntimeConfigDep,
    api_key: _SoftApiKeyDep,
) -> GeneratedKeyResponse:
    """Rotate the MCP key. ALWAYS requires the current API key — rotation
    only makes sense on a configured system, and an anonymous rotate would
    be a lockout vector. Intentionally does NOT accept a session principal:
    key rotation is a privileged operator action requiring explicit proof
    of the current key. Agents must re-add the new key after this."""
    if not runtime.configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no key configured yet — use /config/mcp-key/generate",
        )
    if api_key is None:
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
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
) -> OkResponse:
    """Set or clear the runtime OpenAI key. Bootstrap-or-authed.

    A non-null key must look like an OpenAI secret (`sk-...`). Passing
    null/empty clears the runtime override (falling back to the env key,
    if any). Invalidates the RuntimeConfig cache so embeddings flip on the
    next ingest without a restart."""
    _require_bootstrap_or_authed(runtime, api_key, principal)
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


@router.post("/webhook-secret/generate", response_model=WebhookSecretResponse)
async def generate_webhook_secret(
    runtime: RuntimeConfigDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
) -> WebhookSecretResponse:
    """Generate (or replace) the git-webhook signing secret. Returned ONCE —
    copy it into your GitHub/GitLab webhook settings now. Bootstrap-or-authed
    (open only while the instance is unconfigured)."""
    _require_bootstrap_or_authed(runtime, api_key, principal)
    secret = secrets.token_urlsafe(_MCP_KEY_ENTROPY_BYTES)
    await runtime.repo.set_webhook_secret(secret)
    await runtime.refresh()
    return WebhookSecretResponse(secret=secret)


@router.post("/embedding-mode", response_model=EmbeddingModeResponse)
async def set_embedding_mode(
    body: EmbeddingModeRequest,
    runtime: RuntimeConfigDep,
    state: AppStateDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
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
    _require_bootstrap_or_authed(runtime, api_key, principal)
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
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
) -> OkResponse:
    """Mark the first-run wizard done. Bootstrap-or-authed."""
    _require_bootstrap_or_authed(runtime, api_key, principal)
    await runtime.repo.set_onboarding_completed(True)
    await runtime.refresh()
    return OkResponse()


# ---------------------------------------------------------------------------
# Named, revocable API tokens
# ---------------------------------------------------------------------------
class IssueTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)


class IssuedTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    token: str  # the raw secret — shown ONCE
    token_hint: str


class TokenView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    token_hint: str
    created_at: datetime | None
    last_used_at: datetime | None
    revoked: bool


class TokenListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tokens: list[TokenView]


@router.post("/tokens", response_model=IssuedTokenResponse)
async def issue_token(
    body: IssueTokenRequest,
    runtime: RuntimeConfigDep,
    tokens: TokenCacheDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
) -> IssuedTokenResponse:
    """Mint a named API token. The raw token is returned ONCE — copy it now.
    Works everywhere the MCP key works; revoke it individually later.
    Bootstrap-or-authed (open only while the instance is unconfigured)."""
    _require_bootstrap_or_authed(runtime, api_key, principal)
    raw, row = await tokens.repo.issue(body.name)
    await tokens.refresh()  # the new token is valid immediately
    return IssuedTokenResponse(
        id=row.id, name=row.name, token=raw, token_hint=row.token_hint
    )


@router.get("/tokens", response_model=TokenListResponse)
async def list_tokens(
    runtime: RuntimeConfigDep,
    tokens: TokenCacheDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
) -> TokenListResponse:
    """List named tokens (masked; never the raw value). Bootstrap-or-authed."""
    _require_bootstrap_or_authed(runtime, api_key, principal)
    rows = await tokens.repo.list_all()
    return TokenListResponse(tokens=[
        TokenView(
            id=r.id, name=r.name, token_hint=r.token_hint,
            created_at=r.created_at, last_used_at=r.last_used_at,
            revoked=r.revoked,
        )
        for r in rows
    ])


@router.delete("/tokens/{token_id}", response_model=OkResponse)
async def revoke_token(
    token_id: str,
    runtime: RuntimeConfigDep,
    tokens: TokenCacheDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
) -> OkResponse:
    """Revoke a named token immediately (next request with it fails).
    Bootstrap-or-authed."""
    _require_bootstrap_or_authed(runtime, api_key, principal)
    revoked = await tokens.repo.revoke(token_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown or already-revoked token",
        )
    await tokens.refresh()
    return OkResponse()


# ---------------------------------------------------------------------------
# OAuth / OIDC provider admin CRUD
# ---------------------------------------------------------------------------

def _provider_view(row) -> ProviderView:
    """Map an AuthProviderRow to a ProviderView (secrets masked)."""
    return ProviderView(
        id=row.id,
        provider_type=row.provider_type,
        display_name=row.display_name,
        client_id=row.client_id,
        has_secret=bool(row.client_secret),
        discovery_url=row.discovery_url,
        scopes=row.scopes,
        enabled=row.enabled,
    )


@router.post("/auth/providers", response_model=ProviderView)
async def create_auth_provider(
    body: ProviderCreate,
    runtime: RuntimeConfigDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
    repo: AuthProviderRepoDep,
    registry: OAuthRegistryDep,
) -> ProviderView:
    """Create an identity provider (created DISABLED). Bootstrap-or-authed.

    Returns the new provider view (secrets masked). The OAuthRegistry is
    rebuilt from the enabled list so changes take effect immediately.
    """
    _require_bootstrap_or_authed(runtime, api_key, principal)
    try:
        pt = normalize_provider_type(body.provider_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    pid = secrets.token_urlsafe(8)
    row = await repo.create(
        id=pid,
        provider_type=pt,
        display_name=body.display_name,
        client_id=body.client_id,
        client_secret=body.client_secret,
        discovery_url=body.discovery_url,
        scopes=body.scopes,
        enabled=False,
    )
    registry.rebuild(await repo.list_enabled())
    return _provider_view(row)


@router.get("/auth/providers", response_model=ProviderListResponse)
async def list_auth_providers(
    runtime: RuntimeConfigDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
    repo: AuthProviderRepoDep,
) -> ProviderListResponse:
    """List all configured providers (secrets masked). Bootstrap-or-authed."""
    _require_bootstrap_or_authed(runtime, api_key, principal)
    rows = await repo.list_all()
    return ProviderListResponse(providers=[_provider_view(r) for r in rows])


@router.patch("/auth/providers/{pid}", response_model=ProviderView)
async def update_auth_provider(
    pid: str,
    body: ProviderUpdate,
    runtime: RuntimeConfigDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
    repo: AuthProviderRepoDep,
    registry: OAuthRegistryDep,
) -> ProviderView:
    """Update a provider's configuration. Bootstrap-or-authed.

    Rebuilds the OAuthRegistry so changes to enabled providers take effect
    immediately without a restart.
    """
    _require_bootstrap_or_authed(runtime, api_key, principal)
    if await repo.get(pid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider not found")
    row = await repo.update(
        id=pid,
        display_name=body.display_name,
        client_id=body.client_id,
        client_secret=body.client_secret,
        discovery_url=body.discovery_url,
        scopes=body.scopes,
    )
    registry.rebuild(await repo.list_enabled())
    return _provider_view(row)


@router.post("/auth/providers/{pid}/enable", response_model=ProviderView)
async def set_auth_provider_enabled(
    pid: str,
    body: EnableRequest,
    runtime: RuntimeConfigDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
    repo: AuthProviderRepoDep,
    registry: OAuthRegistryDep,
) -> ProviderView:
    """Enable or disable a provider. Bootstrap-or-authed.

    When enabling, verifies the OAuthRegistry accepted the config. If
    `registry.client_for(pid)` is None after rebuild the provider config is
    invalid and a 400 is returned.
    """
    _require_bootstrap_or_authed(runtime, api_key, principal)
    row = await repo.get(pid)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider not found")
    await repo.set_enabled(id=pid, enabled=body.enabled)
    registry.rebuild(await repo.list_enabled())
    if body.enabled and registry.client_for(pid) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provider configuration is invalid",
        )
    updated = await repo.get(pid)
    assert updated is not None
    return _provider_view(updated)


@router.delete("/auth/providers/{pid}")
async def delete_auth_provider(
    pid: str,
    runtime: RuntimeConfigDep,
    api_key: _SoftApiKeyDep,
    principal: SoftPrincipalDep,
    repo: AuthProviderRepoDep,
    registry: OAuthRegistryDep,
) -> dict:
    """Delete a provider. Bootstrap-or-authed.

    Rebuilds the OAuthRegistry so the removed provider is de-registered
    immediately.
    """
    _require_bootstrap_or_authed(runtime, api_key, principal)
    if await repo.get(pid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="provider not found")
    await repo.delete(pid)
    registry.rebuild(await repo.list_enabled())
    return {"ok": True}
