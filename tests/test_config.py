from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import Settings, get_settings


def test_settings_defaults_match_environment_spec() -> None:
    s = Settings()
    assert s.postgres_url.startswith("postgresql+asyncpg://")
    assert s.qdrant_url == "http://qdrant:6333"
    assert s.neo4j_uri == "bolt://neo4j:7687"
    assert s.redis_url.startswith("redis://")
    assert s.embedding_model == "text-embedding-3-large"
    assert s.primary_llm == "claude-sonnet-4"
    assert s.max_context_tokens == 4000
    assert s.chunk_size == 400
    assert s.chunk_overlap == 40
    assert s.enable_graph_ranking is True


def test_settings_secrets_do_not_leak_in_repr() -> None:
    s = Settings()
    assert "memory-cl-dev" not in repr(s)
    assert s.neo4j_password.get_secret_value() == "memory-cl-dev"


def test_chunk_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValidationError):
        Settings(chunk_size=10, chunk_overlap=10)
    with pytest.raises(ValidationError):
        Settings(chunk_size=10, chunk_overlap=20)


def test_get_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b
