from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from apps.api.state import AppState
from storage import Neo4jClient, PostgresClient, QdrantStorageClient, RedisClient


def get_app_state(request: Request) -> AppState:
    state = getattr(request.app.state, "app_state", None)
    if state is None:
        raise RuntimeError("AppState not initialized — lifespan did not run")
    assert isinstance(state, AppState)
    return state


AppStateDep = Annotated[AppState, Depends(get_app_state)]


def get_postgres(state: AppStateDep) -> PostgresClient:
    return state.postgres


def get_qdrant(state: AppStateDep) -> QdrantStorageClient:
    return state.qdrant


def get_neo4j(state: AppStateDep) -> Neo4jClient:
    return state.neo4j


def get_redis(state: AppStateDep) -> RedisClient:
    return state.redis


PostgresDep = Annotated[PostgresClient, Depends(get_postgres)]
QdrantDep = Annotated[QdrantStorageClient, Depends(get_qdrant)]
Neo4jDep = Annotated[Neo4jClient, Depends(get_neo4j)]
RedisDep = Annotated[RedisClient, Depends(get_redis)]
