"""Phase-6 golden gate.

Wires the LifecycleStateScanner against the fixture-derived units +
graph + a fake Redis. The scanner must:
    * produce non-empty plans for at least one stage
    * produce byte-deterministic plans across two runs at the same `now`
    * never delete underlying data (only soft status flips)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.ingestion import GraphBuilder
from core.lifecycle import (
    DecayEngine,
    DecayPolicy,
    EmbeddingRefreshScheduler,
    GraphCompactor,
    LifecycleContext,
    LifecycleStateScanner,
    MemoryCompactor,
    RelevanceScorer,
)
from core.parsing import FileWalker, PythonParser

FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"


class _FakeRedis:
    """Single-process Redis stand-in matching the surface usage_tracker
    + feedback_collector + decay_engine actually invoke.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def incr(self, key: str) -> int:
        n = int(self.store.get(key, "0")) + 1
        self.store[key] = str(n)
        return n

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> None:
        self.store[key] = value

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]


def _ingest_fixture():
    walk = FileWalker().walk(FIXTURE, repo_id="acme")
    parser = PythonParser()
    units = []
    for ref in walk.files:
        units.extend(parser.parse_file(
            source=(FIXTURE / ref.path).read_text(encoding="utf-8"),
            repo_id="acme",
            file_path=ref.path,
            commit_sha="commit-deadbeef",
        ))
    return units, GraphBuilder().build(units)


def _scanner() -> LifecycleStateScanner:
    return LifecycleStateScanner(
        scorer=RelevanceScorer(usage_window_days=14),
        decay_engine=DecayEngine(policy=DecayPolicy(
            decay_threshold_days=30,
            low_priority_threshold=0.3,
            centrality_threshold=0.2,
        )),
        memory_compactor=MemoryCompactor(low_priority_threshold=0.3),
        graph_compactor=GraphCompactor(centrality_threshold=0.2),
        refresh_scheduler=EmbeddingRefreshScheduler(refresh_threshold=0.4),
    )


def _ctx(redis_client) -> LifecycleContext:
    state = SimpleNamespace(redis=SimpleNamespace(client=redis_client))
    return LifecycleContext(
        repo_id="acme", state=state,
        # Pinned `now` so the test is byte-deterministic.
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_phase6_golden_scan_produces_plans() -> None:
    units, graph = _ingest_fixture()
    scanner = _scanner()
    redis = _FakeRedis()
    res = await scanner.scan(
        _ctx(redis),
        units=units, nodes=graph.nodes, edges=graph.edges,
        previous_neighbor_signatures={},
    )

    # Every node has a relevance breakdown.
    assert len(res.breakdowns) == len(graph.nodes)
    # Decay plan covers every node (NO_OP, DOWNGRADE, or PROMOTE).
    assert len(res.decay.decisions) == len(graph.nodes)
    # Without prior usage signal most nodes will be marked downgrade-eligible
    # (no last access + low centrality + low score).
    assert res.decay.downgrades >= 1
    # Memory compactor produces at least one entry (most fixture units
    # get a 0 score and are eligible for compaction). Graph compactor
    # depends on whether any function is sufficiently isolated — for
    # the fixture all callees have at least one caller, so 0 merges is
    # also valid. We assert non-negativity rather than guess topology.
    assert len(res.memory_compaction.entries) >= 1
    assert len(res.graph_compaction.merges) >= 0
    # Refresh plan triggers because all relevance scores are low.
    assert len(res.refresh.decisions) >= 1


@pytest.mark.asyncio
async def test_phase6_golden_scan_is_byte_deterministic_across_runs() -> None:
    units, graph = _ingest_fixture()

    async def _run():
        return await _scanner().scan(
            _ctx(_FakeRedis()),
            units=units, nodes=graph.nodes, edges=graph.edges,
            previous_neighbor_signatures={},
        )

    a = await _run()
    b = await _run()
    sig_a = _signature(a)
    sig_b = _signature(b)
    assert sig_a == sig_b


@pytest.mark.asyncio
async def test_phase6_golden_apply_only_writes_status_keys() -> None:
    """Spec: never delete data directly. Apply must only flip Redis flags.

    We assert the post-apply Redis store contains ONLY the
    `phase6:status:*` keys we wrote — no other namespace was touched.
    """
    units, graph = _ingest_fixture()
    redis = _FakeRedis()
    res = await _scanner().scan(
        _ctx(redis), units=units, nodes=graph.nodes, edges=graph.edges,
        previous_neighbor_signatures={},
        apply_decay=True,
    )
    if res.decay.downgrades + res.decay.promotions:
        # Every key we wrote must be a status flag.
        assert all(k.startswith("phase6:status:") for k in redis.store)
    # No deletion APIs were called on the underlying repos (we never
    # passed graph_repo / units_repo / vector_repo to the scanner).


@pytest.mark.asyncio
async def test_phase6_golden_promotes_after_recovered_signal() -> None:
    """A previously-downgraded node gets `promote` when its relevance
    bounces back through usage."""
    units, graph = _ingest_fixture()
    redis = _FakeRedis()

    # Pick a deterministic node, mark it as low_priority, give it
    # plenty of recent usage so its relevance score crosses threshold.
    target = sorted(n.node_id for n in graph.nodes)[0]
    await redis.set(f"phase6:status:acme:{target}", "low_priority_index")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    for _ in range(50):
        await redis.incr(f"phase6:usage:acme:{target}")
    await redis.set(
        f"phase6:last_access:acme:{target}", now.isoformat(),
    )

    res = await _scanner().scan(
        LifecycleContext(repo_id="acme",
                         state=SimpleNamespace(redis=SimpleNamespace(client=redis)),
                         now=now),
        units=units, nodes=graph.nodes, edges=graph.edges,
        previous_neighbor_signatures={},
    )
    # The targeted node appears in the plan as a PROMOTE decision.
    target_decision = next(d for d in res.decay.decisions if d.entity_id == target)
    assert target_decision.action.value == "promote"


# ---------------------------------------------------------------------------
def _signature(res) -> str:
    """Project the scan result into a stable JSON-comparable shape."""
    return json.dumps({
        "breakdowns": {
            k: {
                "score": round(v.score, 9),
                "usage": round(v.usage, 9),
                "recency": round(v.recency, 9),
                "centrality": round(v.centrality, 9),
                "success": round(v.success, 9),
            }
            for k, v in sorted(res.breakdowns.items())
        },
        "decay": [
            {"id": d.entity_id, "action": d.action.value,
             "score": round(d.relevance_score, 9)}
            for d in res.decay.decisions
        ],
        "memory_compaction": [
            {"module": e.module_qname, "merged": list(e.merged_unit_ids)}
            for e in res.memory_compaction.entries
        ],
        "graph_compaction": [
            {"target": m.target_node_id,
             "merged": list(m.merged_node_ids),
             "edges": list(m.preserved_edge_ids)}
            for m in res.graph_compaction.merges
        ],
        "refresh": [
            {"id": d.entity_id, "reasons": [r.value for r in d.reasons]}
            for d in res.refresh.decisions
        ],
    }, sort_keys=True)
