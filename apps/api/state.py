from __future__ import annotations

from dataclasses import dataclass

from core.embeddings import DeterministicEmbedder, Embedder
from storage import (
    Neo4jClient,
    Neo4jGraphRepository,
    PostgresClient,
    PostgresIngestionRepository,
    QdrantStorageClient,
    QdrantVectorRepository,
    RedisClient,
)


@dataclass
class AppState:
    """Container for long-lived clients + repositories attached to `FastAPI.state`.

    Holding clients in a dataclass (rather than ad-hoc attributes on
    `app.state`) gives us static typing in dependencies and a single place
    to extend when new backends are added in later phases.
    """

    postgres: PostgresClient
    qdrant: QdrantStorageClient
    neo4j: Neo4jClient
    redis: RedisClient
    units_repo: PostgresIngestionRepository
    graph_repo: Neo4jGraphRepository
    vector_repo: QdrantVectorRepository
    # Phase 4: retrieval-side dependencies. The default embedder is the
    # deterministic Phase-3 embedder; Phase 5 will swap in a model-backed
    # one without touching this contract.
    embedder: Embedder

    @classmethod
    def with_default_embedder(
        cls,
        *,
        postgres: PostgresClient,
        qdrant: QdrantStorageClient,
        neo4j: Neo4jClient,
        redis: RedisClient,
        units_repo: PostgresIngestionRepository,
        graph_repo: Neo4jGraphRepository,
        vector_repo: QdrantVectorRepository,
        embedding_dimension: int = 1536,
    ) -> AppState:
        return cls(
            postgres=postgres,
            qdrant=qdrant,
            neo4j=neo4j,
            redis=redis,
            units_repo=units_repo,
            graph_repo=graph_repo,
            vector_repo=vector_repo,
            embedder=DeterministicEmbedder(dimension=embedding_dimension),
        )
