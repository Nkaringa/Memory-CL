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
    # Phase 4: retrieval-side dependencies. The query embedder MUST live
    # in the same vector space as the document embedder used at ingest —
    # lifespan wires an OpenAIEmbedder when embeddings are enabled and
    # the deterministic fallback otherwise.
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
        embedder: Embedder | None = None,
    ) -> AppState:
        """Build an AppState, defaulting the embedder when none is given.

        Pass `embedder` to wire a model-backed query embedder (the
        production path when embeddings are enabled); leave it None for
        the deterministic fallback (tests, embeddings-disabled deploys).
        """
        return cls(
            postgres=postgres,
            qdrant=qdrant,
            neo4j=neo4j,
            redis=redis,
            units_repo=units_repo,
            graph_repo=graph_repo,
            vector_repo=vector_repo,
            embedder=(
                embedder
                if embedder is not None
                else DeterministicEmbedder(dimension=embedding_dimension)
            ),
        )
