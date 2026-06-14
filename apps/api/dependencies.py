from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from apps.api.state import AppState
from core.auth.session_cache import SessionCache
from core.config_runtime import RuntimeConfig
from core.token_cache import TokenCache
from storage import (
    Neo4jClient,
    PostgresClient,
    QdrantStorageClient,
    RedisClient,
    RepoRegistryRepository,
)
from core.auth.oauth_registry import OAuthRegistry
from storage.repositories import AuthProviderRepository, FederatedIdentityRepository, InvitationRepository, MembershipRepository, OrgRepository, RepoGrantRepository, SessionRepository, TeamRepository, UserRepository


def get_app_state(request: Request) -> AppState:
    state = getattr(request.app.state, "app_state", None)
    if state is None:
        raise RuntimeError("AppState not initialized — lifespan did not run")
    assert isinstance(state, AppState)
    return state


AppStateDep = Annotated[AppState, Depends(get_app_state)]


def get_runtime_config(request: Request) -> RuntimeConfig:
    """The runtime config (Postgres-over-env) attached during lifespan.

    Raises if missing — the config router + embedder paths require it.
    Auth (`apps.mcp.auth`) reads it defensively via `getattr` so test
    apps without it fall back to env; this strict accessor is for the
    surfaces that are only ever mounted under the full lifespan.
    """
    runtime = getattr(request.app.state, "runtime_config", None)
    if runtime is None:
        raise RuntimeError("RuntimeConfig not initialized — lifespan did not run")
    assert isinstance(runtime, RuntimeConfig)
    return runtime


RuntimeConfigDep = Annotated[RuntimeConfig, Depends(get_runtime_config)]


def get_postgres(state: AppStateDep) -> PostgresClient:
    return state.postgres


def get_qdrant(state: AppStateDep) -> QdrantStorageClient:
    return state.qdrant


def get_neo4j(state: AppStateDep) -> Neo4jClient:
    return state.neo4j


def get_redis(state: AppStateDep) -> RedisClient:
    return state.redis


def get_token_cache(request: Request) -> TokenCache:
    """The named-API-token cache attached during lifespan."""
    cache = getattr(request.app.state, "token_cache", None)
    if cache is None:
        raise RuntimeError("TokenCache not initialized — lifespan did not run")
    assert isinstance(cache, TokenCache)
    return cache


def get_repo_registry(request: Request) -> RepoRegistryRepository:
    """The Phase-3 freshness repo registry attached during lifespan.

    Not an isinstance check: lite mode supplies a duck-compatible SQLite
    repo (same surface, different class), so we only assert it's wired.
    """
    registry = getattr(request.app.state, "repo_registry", None)
    if registry is None:
        raise RuntimeError("RepoRegistryRepository not initialized — lifespan did not run")
    return registry  # type: ignore[no-any-return]


PostgresDep = Annotated[PostgresClient, Depends(get_postgres)]
QdrantDep = Annotated[QdrantStorageClient, Depends(get_qdrant)]
Neo4jDep = Annotated[Neo4jClient, Depends(get_neo4j)]
RedisDep = Annotated[RedisClient, Depends(get_redis)]
RepoRegistryDep = Annotated[RepoRegistryRepository, Depends(get_repo_registry)]
TokenCacheDep = Annotated[TokenCache, Depends(get_token_cache)]


def get_org_repo(state: AppStateDep) -> OrgRepository:
    if state.org_repo is None:
        raise RuntimeError("OrgRepository not initialized — lifespan did not run")
    return state.org_repo


def get_user_repo(state: AppStateDep) -> UserRepository:
    if state.user_repo is None:
        raise RuntimeError("UserRepository not initialized — lifespan did not run")
    return state.user_repo


def get_membership_repo(state: AppStateDep) -> MembershipRepository:
    if state.membership_repo is None:
        raise RuntimeError("MembershipRepository not initialized — lifespan did not run")
    return state.membership_repo


def get_session_repo(state: AppStateDep) -> SessionRepository:
    if state.session_repo is None:
        raise RuntimeError("SessionRepository not initialized — lifespan did not run")
    return state.session_repo


def get_session_cache(request: Request) -> SessionCache:
    """The session-ID cache attached during lifespan (mirrors get_token_cache)."""
    cache = getattr(request.app.state, "session_cache", None)
    if cache is None:
        raise RuntimeError("SessionCache not initialized — lifespan did not run")
    assert isinstance(cache, SessionCache)
    return cache


OrgRepoDep = Annotated[OrgRepository, Depends(get_org_repo)]
UserRepoDep = Annotated[UserRepository, Depends(get_user_repo)]
MembershipRepoDep = Annotated[MembershipRepository, Depends(get_membership_repo)]
SessionRepoDep = Annotated[SessionRepository, Depends(get_session_repo)]
SessionCacheDep = Annotated[SessionCache, Depends(get_session_cache)]


def get_auth_provider_repo(state: AppStateDep) -> AuthProviderRepository:
    if state.auth_provider_repo is None:
        raise RuntimeError("AuthProviderRepository not initialized — lifespan did not run")
    return state.auth_provider_repo


def get_federated_identity_repo(state: AppStateDep) -> FederatedIdentityRepository:
    if state.federated_identity_repo is None:
        raise RuntimeError("FederatedIdentityRepository not initialized — lifespan did not run")
    return state.federated_identity_repo


def get_oauth_registry(request: Request) -> OAuthRegistry:
    """The OAuthRegistry attached during lifespan (mirrors get_session_cache)."""
    registry = getattr(request.app.state, "oauth_registry", None)
    if registry is None:
        raise RuntimeError("OAuthRegistry not initialized — lifespan did not run")
    assert isinstance(registry, OAuthRegistry)
    return registry


AuthProviderRepoDep = Annotated[AuthProviderRepository, Depends(get_auth_provider_repo)]
FederatedIdentityRepoDep = Annotated[FederatedIdentityRepository, Depends(get_federated_identity_repo)]
OAuthRegistryDep = Annotated[OAuthRegistry, Depends(get_oauth_registry)]


def get_team_repo(state: AppStateDep) -> TeamRepository:
    if state.team_repo is None:
        raise RuntimeError("TeamRepository not initialized — lifespan did not run")
    return state.team_repo


def get_repo_grant_repo(state: AppStateDep) -> RepoGrantRepository:
    if state.repo_grant_repo is None:
        raise RuntimeError("RepoGrantRepository not initialized — lifespan did not run")
    return state.repo_grant_repo


def get_invitation_repo(state: AppStateDep) -> InvitationRepository:
    if state.invitation_repo is None:
        raise RuntimeError("InvitationRepository not initialized — lifespan did not run")
    return state.invitation_repo


TeamRepoDep = Annotated[TeamRepository, Depends(get_team_repo)]
RepoGrantRepoDep = Annotated[RepoGrantRepository, Depends(get_repo_grant_repo)]
InvitationRepoDep = Annotated[InvitationRepository, Depends(get_invitation_repo)]
