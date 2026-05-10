from __future__ import annotations

import pytest

from core.scaling import (
    GraphShardRouter,
    IngestionDistributor,
    RetrievalCache,
    VectorShardRouter,
    cache_key_for_query,
)
from core.scaling.ingestion_distributor import IngestRequest


# =========================================================================
#                              Shard routers
# =========================================================================
def test_graph_router_is_deterministic() -> None:
    r = GraphShardRouter(shard_count=8)
    a = r.route(repo_id="acme")
    b = r.route(repo_id="acme")
    assert a == b
    assert 0 <= a.shard_index < 8


def test_graph_router_distributes_across_shards() -> None:
    r = GraphShardRouter(shard_count=4)
    seen = {r.route(repo_id=f"repo-{i}").shard_index for i in range(40)}
    # Across 40 ids we expect to hit multiple shards (sanity, not load).
    assert len(seen) > 1


def test_graph_router_node_route_ignores_node_id() -> None:
    """Spec invariant: all nodes in a repo land on the same shard."""
    r = GraphShardRouter(shard_count=4)
    a = r.route_node(repo_id="acme", node_id="u-aaa")
    b = r.route_node(repo_id="acme", node_id="u-zzz")
    assert a.shard_id == b.shard_id


def test_vector_router_co_locates_with_graph() -> None:
    """Vector + graph routers MUST give the same shard index for the
    same repo_id — this is what keeps Postgres↔Neo4j↔Qdrant joins
    local within a shard."""
    g = GraphShardRouter(shard_count=8)
    v = VectorShardRouter(shard_count=8)
    for repo_id in ["r1", "r2", "r3", "acme", "tenant-100"]:
        assert g.route(repo_id=repo_id).shard_index == \
               v.route(repo_id=repo_id).shard_index


def test_vector_router_collection_name_includes_shard() -> None:
    v = VectorShardRouter(shard_count=4)
    assignment = v.route(repo_id="acme")
    assert assignment.collection_name.startswith("repo_acme_s")


def test_routers_reject_zero_shard_count() -> None:
    with pytest.raises(ValueError):
        GraphShardRouter(shard_count=0)
    with pytest.raises(ValueError):
        VectorShardRouter(shard_count=0)


# =========================================================================
#                          IngestionDistributor
# =========================================================================
def test_distributor_assigns_repos_to_their_shards() -> None:
    dist = IngestionDistributor(
        graph_router=GraphShardRouter(shard_count=4),
        vector_router=VectorShardRouter(shard_count=4),
    )
    plan = dist.plan([
        IngestRequest(repo_id="b", repo_path="/b", commit_sha="c"),
        IngestRequest(repo_id="a", repo_path="/a", commit_sha="c"),
    ])
    # Determinism: sorted by repo_id.
    assert [a.repo_id for a in plan.assignments] == ["a", "b"]
    # Each assignment carries the shard ids derived from its repo.
    for a in plan.assignments:
        assert a.graph_shard_id.startswith("graph-")
        assert a.vector_shard_id.startswith("vector-")
        assert a.vector_collection.startswith("repo_")


def test_distributor_is_byte_deterministic() -> None:
    dist = IngestionDistributor(
        graph_router=GraphShardRouter(shard_count=4),
        vector_router=VectorShardRouter(shard_count=4),
    )
    reqs = [IngestRequest(repo_id=f"r-{i}", repo_path=f"/p{i}", commit_sha="c")
            for i in range(5)]
    a = dist.plan(reqs)
    b = dist.plan(list(reversed(reqs)))
    assert [x.graph_shard_id for x in a.assignments] == \
           [x.graph_shard_id for x in b.assignments]


# =========================================================================
#                           RetrievalCache
# =========================================================================
def test_cache_hit_after_put() -> None:
    cache = RetrievalCache(max_size=10, ttl_seconds=60)
    cache.put("k", "v", version_token="v0", now=100.0)
    assert cache.get("k", version_token="v0", now=110.0) == "v"
    assert cache.hits == 1


def test_cache_miss_when_version_changed() -> None:
    cache = RetrievalCache(max_size=10, ttl_seconds=60)
    cache.put("k", "v", version_token="v0", now=100.0)
    assert cache.get("k", version_token="v1", now=110.0) is None
    assert cache.misses == 1


def test_cache_miss_when_expired() -> None:
    cache = RetrievalCache(max_size=10, ttl_seconds=5)
    cache.put("k", "v", version_token="v0", now=100.0)
    assert cache.get("k", version_token="v0", now=110.0) is None


def test_cache_lru_eviction_when_full() -> None:
    cache = RetrievalCache(max_size=2, ttl_seconds=60)
    cache.put("a", 1, version_token="v0", now=100.0)
    cache.put("b", 2, version_token="v0", now=100.0)
    cache.put("c", 3, version_token="v0", now=100.0)
    # 'a' was evicted.
    assert cache.get("a", version_token="v0", now=100.0) is None
    assert cache.get("b", version_token="v0", now=100.0) == 2
    assert cache.get("c", version_token="v0", now=100.0) == 3


def test_cache_invalidate_version_evicts_only_matching() -> None:
    cache = RetrievalCache(max_size=10, ttl_seconds=60)
    cache.put("k1", 1, version_token="v0", now=100.0)
    cache.put("k2", 2, version_token="v1", now=100.0)
    n = cache.invalidate_version("v0")
    assert n == 1
    assert cache.get("k1", version_token="v0", now=100.0) is None
    assert cache.get("k2", version_token="v1", now=100.0) == 2


def test_cache_key_is_deterministic_under_input_reorder() -> None:
    a = cache_key_for_query(
        repo_id="r", query_text="x", top_k=5,
        unit_kinds=["b", "a"], seed_unit_ids=["z", "a"], version_token="v0",
    )
    b = cache_key_for_query(
        repo_id="r", query_text="x", top_k=5,
        unit_kinds=["a", "b"], seed_unit_ids=["a", "z"], version_token="v0",
    )
    assert a == b


def test_cache_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError):
        RetrievalCache(max_size=0, ttl_seconds=60)
    with pytest.raises(ValueError):
        RetrievalCache(max_size=10, ttl_seconds=0)
