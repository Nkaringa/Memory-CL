from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel values that historically appeared in dev .env templates and
# MUST NOT survive into a production / staging deployment. The strict
# validator below rejects them at process start-up.
_INSECURE_SENTINELS: frozenset[str] = frozenset({
    "memory-cl-dev",
    "postgres",
    "changeme",
    "change-me",
    "password",
    "neo4j",
    "",
})


class StrictConfigError(ValueError):
    """Raised when production / staging settings fail validation.

    Settings is constructed during app startup; raising here aborts
    `lifespan` immediately so the orchestrator restarts the container
    instead of serving traffic with a misconfigured runtime.
    """


class Settings(BaseSettings):
    """Single source of truth for runtime configuration.

    Values are loaded from environment variables (and optionally a .env file).
    Subclassing or mutating Settings at runtime is not supported — use
    `get_settings()` cache and clear it for tests.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----- Storage -----
    postgres_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@postgres:5432/memory",
        description="SQLAlchemy async URL for Postgres",
    )
    qdrant_url: str = Field(default="http://qdrant:6333")
    neo4j_uri: str = Field(default="bolt://neo4j:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: SecretStr = Field(default=SecretStr("memory-cl-dev"))
    redis_url: str = Field(default="redis://redis:6379/0")

    # ----- LLM / embedding -----
    openai_api_key: SecretStr | None = Field(default=None)
    anthropic_api_key: SecretStr | None = Field(default=None)
    # 1536-dim — matches the existing Qdrant collections, no migration.
    embedding_model: str = Field(default="text-embedding-3-small")
    primary_llm: str = Field(default="claude-sonnet-4")

    @property
    def embeddings_enabled(self) -> bool:
        """True when a real embedding provider key is configured.

        Drives whether ingest wires an `OpenAIEmbedder` (real semantic
        vectors) or leaves placeholder points (the pre-Phase-3 behavior).
        Empty / whitespace-only keys do not count as configured.
        """
        return (
            self.openai_api_key is not None
            and bool(self.openai_api_key.get_secret_value().strip())
        )

    # ----- Retrieval -----
    max_context_tokens: int = Field(default=4000, gt=0)
    chunk_size: int = Field(default=400, gt=0)
    chunk_overlap: int = Field(default=40, ge=0)
    max_graph_traversal_depth: int = Field(default=3, gt=0, le=10)
    default_top_k: int = Field(default=10, gt=0, le=200)

    # ----- MCP (Phase 5) -----
    # If unset, MCP runs in dev mode (no auth). Production deployments
    # MUST provide a key; the auth dependency rejects requests when set.
    mcp_api_key: SecretStr | None = Field(default=None)
    mcp_session_ttl_seconds: int = Field(default=3600, gt=0)

    # ----- Lifecycle (Phase 6) -----
    # Days without access before an entity is eligible for decay.
    lifecycle_decay_threshold_days: int = Field(default=30, gt=0, le=3650)
    # Relevance score below which an entity gets the low_priority flag.
    lifecycle_low_priority_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    # Relevance score below which embedding refresh is scheduled.
    lifecycle_refresh_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    # Graph centrality below which a node is a compaction candidate.
    lifecycle_centrality_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    # Window (days) over which usage signals are summed.
    lifecycle_usage_window_days: int = Field(default=14, gt=0, le=365)

    # ----- Distributed scale (Phase 7) -----
    # Worker pool concurrency for distributed ingestion / retrieval.
    scale_worker_count: int = Field(default=4, gt=0, le=64)
    # Number of shards used by graph + vector shard routers.
    scale_shard_count: int = Field(default=4, gt=0, le=256)
    # In-memory retrieval cache size (entries) and TTL.
    scale_retrieval_cache_size: int = Field(default=1024, gt=0, le=1_000_000)
    scale_retrieval_cache_ttl_seconds: int = Field(default=300, gt=0, le=86_400)
    # Per-(caller, resource) requests-per-second cap for the rate limiter.
    scale_default_rate_per_second: float = Field(default=20.0, gt=0.0)
    # Backpressure trigger: queue depth ratio at which we throttle.
    scale_backpressure_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    # Per-batch micro-task flush window.
    scale_batch_max_size: int = Field(default=32, gt=0, le=1024)
    scale_batch_max_wait_ms: int = Field(default=20, gt=0, le=10_000)

    # ----- Production deployment (Phase 9) -----
    environment: Literal["development", "staging", "production"] = "development"
    # If true, mutating endpoints (/ingest, /retrieve writes) return 503.
    safe_mode_enabled: bool = False
    # Mount the read-only inspection UI at /ui.
    ui_enabled: bool = True
    # Enforce strict bootstrap validation — production always strict;
    # dev can opt out for faster iteration.
    strict_bootstrap: bool = False
    # Public-facing service identifier that surfaces in /status responses.
    service_label: str = Field(default="memory-cl", min_length=1, max_length=64)

    # ----- Feature flags -----
    enable_graph_ranking: bool = True
    enable_incremental_indexing: bool = True
    enable_context_compression: bool = True

    # ----- Observability -----
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    otel_enabled: bool = True
    otel_service_name: str = "memory-cl"
    otel_exporter_otlp_endpoint: str | None = None

    # ----- API -----
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, gt=0, lt=65536)

    @field_validator("chunk_overlap")
    @classmethod
    def _overlap_must_be_lt_chunk_size(cls, v: int, info: object) -> int:
        # Pydantic v2 passes a ValidationInfo with .data containing previously-validated fields.
        data = getattr(info, "data", {}) or {}
        size = data.get("chunk_size")
        if size is not None and v >= size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return v

    # ------------------------------------------------------------------
    # Phase-10 strict environment validation
    #
    # Production and staging MUST satisfy a stricter contract than dev.
    # The boot script (scripts/boot.sh) already verifies process-level
    # env presence, but the *value* checks below catch leftover dev
    # placeholders (e.g. NEO4J_PASSWORD=memory-cl-dev) that the shell
    # guard can't see.
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _enforce_environment_contract(self) -> Settings:
        env = self.environment
        if env == "development":
            return self  # dev is permissive by design

        problems: list[str] = []

        # Secrets that must never carry a dev sentinel into staging/prod.
        neo4j_pw = self.neo4j_password.get_secret_value()
        if neo4j_pw.lower() in _INSECURE_SENTINELS:
            problems.append(
                f"NEO4J_PASSWORD must be set to a non-default secret in {env}",
            )

        if self.mcp_api_key is None or not self.mcp_api_key.get_secret_value().strip():
            problems.append(
                f"MCP_API_KEY is required in {env} (the MCP surface refuses "
                "unauthenticated requests when this is set; in dev it is "
                "intentionally optional)",
            )
        elif self.mcp_api_key.get_secret_value().strip().lower() in _INSECURE_SENTINELS:
            problems.append(
                f"MCP_API_KEY in {env} is set to a placeholder sentinel value",
            )

        # Storage URLs must point somewhere — empty strings or default
        # localhost loopbacks are usually a sign of a missing .env.
        if not self.postgres_url.strip():
            problems.append("POSTGRES_URL is empty")
        if not self.qdrant_url.strip():
            problems.append("QDRANT_URL is empty")
        if not self.neo4j_uri.strip():
            problems.append("NEO4J_URI is empty")
        if not self.redis_url.strip():
            problems.append("REDIS_URL is empty")

        # Production MUST run JSON-formatted logs (one event per line)
        # and MUST enable strict bootstrap so the 8-stage health gate
        # actually fails the boot rather than degrading silently.
        if env == "production":
            if self.log_format != "json":
                problems.append(
                    "LOG_FORMAT must be 'json' in production (set LOG_FORMAT=json)",
                )
            if not self.strict_bootstrap:
                problems.append(
                    "STRICT_BOOTSTRAP must be true in production "
                    "(set STRICT_BOOTSTRAP=true)",
                )
            if not self.otel_enabled:
                problems.append(
                    "OTEL_ENABLED must be true in production "
                    "(set OTEL_ENABLED=true)",
                )

        if problems:
            joined = "\n  - " + "\n  - ".join(problems)
            raise StrictConfigError(
                f"Settings validation failed for environment={env}:{joined}\n\n"
                f"Configure these via .env.{env} or your secret manager. "
                f"See docs DEPLOYMENT.md for the required-vars matrix."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings accessor."""
    return Settings()
