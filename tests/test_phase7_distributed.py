from __future__ import annotations

import asyncio

import pytest

from infra.distributed import (
    LoadBalancer,
    RoutingStrategy,
    ShardManager,
    ShardTopology,
    TaskPriority,
    TaskScheduler,
    WorkerPool,
)


# =========================================================================
#                              ShardManager
# =========================================================================
def test_topology_round_robin_assigns_evenly() -> None:
    topo = ShardTopology.round_robin(
        shard_count=4, replicas=("r-1", "r-2"),
    )
    mapping = topo.shard_to_replica
    assert sorted(mapping) == ["shard-0", "shard-1", "shard-2", "shard-3"]
    counts: dict[str, int] = {}
    for r in mapping.values():
        counts[r] = counts.get(r, 0) + 1
    assert counts == {"r-1": 2, "r-2": 2}


def test_shard_manager_resolves_repo_to_replica() -> None:
    topo = ShardTopology.round_robin(shard_count=4, replicas=("r-1", "r-2"))
    sm = ShardManager(topology=topo)
    # Same repo always lands on the same replica.
    assert sm.replica_for("acme") == sm.replica_for("acme")


def test_shard_manager_lists_shards_per_replica() -> None:
    topo = ShardTopology.round_robin(shard_count=4, replicas=("r-1", "r-2"))
    sm = ShardManager(topology=topo)
    s1 = set(sm.shards_for_replica("r-1"))
    s2 = set(sm.shards_for_replica("r-2"))
    assert s1 | s2 == set(topo.shard_to_replica)
    assert not s1 & s2  # disjoint


def test_topology_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        ShardTopology.round_robin(shard_count=0, replicas=("r",))
    with pytest.raises(ValueError):
        ShardTopology.round_robin(shard_count=4, replicas=())


# =========================================================================
#                              WorkerPool
# =========================================================================
@pytest.mark.asyncio
async def test_worker_pool_bounds_concurrency() -> None:
    pool = WorkerPool(worker_count=2)
    inflight_observed: list[int] = []
    counter = {"v": 0}

    async def task(_):
        counter["v"] += 1
        inflight_observed.append(counter["v"])
        await asyncio.sleep(0.01)
        counter["v"] -= 1

    await pool.map(task, list(range(8)))
    assert max(inflight_observed) <= 2


@pytest.mark.asyncio
async def test_worker_pool_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    async def flaky(_):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    pool = WorkerPool(worker_count=1, max_retries=3, backoff_base_ms=1.0)
    result = await pool.submit(flaky, 0)
    assert result == "ok"
    stats = pool.stats
    assert stats.completed == 1
    assert stats.retried == 2


@pytest.mark.asyncio
async def test_worker_pool_raises_after_exhausting_retries() -> None:
    async def always_fail(_):
        raise RuntimeError("nope")

    pool = WorkerPool(worker_count=1, max_retries=2, backoff_base_ms=1.0)
    with pytest.raises(RuntimeError):
        await pool.submit(always_fail, 0)
    assert pool.stats.failed == 1


def test_worker_pool_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        WorkerPool(worker_count=0)
    with pytest.raises(ValueError):
        WorkerPool(worker_count=1, max_retries=-1)


# =========================================================================
#                              TaskScheduler
# =========================================================================
@pytest.mark.asyncio
async def test_scheduler_dispatches_higher_priority_first() -> None:
    pool = WorkerPool(worker_count=1)
    sched = TaskScheduler(pool=pool)
    order: list[str] = []

    async def record(name: str) -> str:
        order.append(name)
        return name

    # Submit lowest-priority FIRST so we can prove priority ordering wins.
    f_bg = asyncio.create_task(sched.submit(
        record, "background", priority=TaskPriority.BACKGROUND,
    ))
    f_hi = asyncio.create_task(sched.submit(
        record, "high", priority=TaskPriority.HIGH,
    ))
    f_critical = asyncio.create_task(sched.submit(
        record, "critical", priority=TaskPriority.CRITICAL,
    ))
    await asyncio.gather(f_bg, f_hi, f_critical)
    # The first task to be popped is the highest priority.
    assert order[0] == "critical"


@pytest.mark.asyncio
async def test_scheduler_preserves_fifo_within_priority_band() -> None:
    pool = WorkerPool(worker_count=1)
    sched = TaskScheduler(pool=pool)
    order: list[int] = []

    async def record(i: int) -> int:
        order.append(i)
        return i

    futures = [
        asyncio.create_task(sched.submit(record, i, priority=TaskPriority.NORMAL))
        for i in range(5)
    ]
    await asyncio.gather(*futures)
    assert order == [0, 1, 2, 3, 4]


# =========================================================================
#                              LoadBalancer
# =========================================================================
def test_hash_balancer_is_deterministic() -> None:
    lb = LoadBalancer(replicas=("a", "b", "c"), strategy=RoutingStrategy.HASH)
    assert lb.route(key="x") == lb.route(key="x")


def test_round_robin_balancer_cycles() -> None:
    lb = LoadBalancer(replicas=("a", "b"), strategy=RoutingStrategy.ROUND_ROBIN)
    assert [lb.route() for _ in range(4)] == ["a", "b", "a", "b"]


def test_balancer_replicas_sorted() -> None:
    lb = LoadBalancer(replicas=("z", "a", "m"))
    assert lb.replicas == ("a", "m", "z")


def test_balancer_rejects_empty_replicas() -> None:
    with pytest.raises(ValueError):
        LoadBalancer(replicas=())
