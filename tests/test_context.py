from __future__ import annotations

import pytest

from core.context import ContextAssembler, ContextOptimizer
from core.context.context_assembler import AssemblyOptions
from schemas import ContextEntry, RankedResult, RankingFeatures, RetrievalChannel


# ---------- ContextOptimizer ----------------------------------------------
def _entry(id_: str, type_: str, score: float, payload: int = 1) -> ContextEntry:
    return ContextEntry(id=id_, type=type_, score=score, data={"k": "x" * payload})


def test_optimizer_dedups_keeping_highest_scoring() -> None:
    e1 = _entry("u1", "logic", 0.4)
    e2 = _entry("u1", "logic", 0.7)
    e3 = _entry("u2", "logic", 0.2)
    out = ContextOptimizer(max_tokens=1000).optimize([e1, e2, e3])
    assert {e.id for e in out} == {"u1", "u2"}
    by_id = {e.id: e for e in out}
    assert by_id["u1"].score == 0.7


def test_optimizer_priority_order_is_constraints_first() -> None:
    entries = [
        _entry("c1", "code", 0.9),
        _entry("a1", "architecture", 0.5),
        _entry("r1", "risk", 0.5),
        _entry("k1", "constraint", 0.5),
        _entry("l1", "logic", 0.5),
    ]
    out = ContextOptimizer(max_tokens=1000).optimize(entries)
    types = [e.type for e in out]
    # constraints > risks > architecture > logic > code
    assert types == ["constraint", "risk", "architecture", "logic", "code"]


def test_optimizer_within_priority_orders_by_score_desc_then_id() -> None:
    entries = [
        _entry("zzz", "logic", 0.5),
        _entry("aaa", "logic", 0.5),
        _entry("mmm", "logic", 0.9),
    ]
    out = ContextOptimizer(max_tokens=1000).optimize(entries)
    assert [e.id for e in out] == ["mmm", "aaa", "zzz"]


def test_optimizer_enforces_token_budget() -> None:
    """Each entry costs about (id_len + data_len)/4 tokens."""
    big = _entry("huge", "logic", 0.9, payload=400)        # ~100 tokens
    small_a = _entry("ax", "logic", 0.8, payload=20)       # ~5 tokens
    small_b = _entry("bx", "logic", 0.7, payload=20)
    out = ContextOptimizer(max_tokens=20).optimize([big, small_a, small_b])
    # `huge` exceeds budget alone; smaller entries fit.
    assert "huge" not in {e.id for e in out}
    assert {"ax", "bx"} == {e.id for e in out}


def test_optimizer_rejects_invalid_budget() -> None:
    with pytest.raises(ValueError):
        ContextOptimizer(max_tokens=0)


def test_optimizer_is_deterministic_for_unsorted_input() -> None:
    a = [_entry("u1", "logic", 0.5), _entry("u2", "code", 0.5)]
    b = [_entry("u2", "code", 0.5), _entry("u1", "logic", 0.5)]
    opt = ContextOptimizer(max_tokens=1000)
    assert [e.id for e in opt.optimize(a)] == [e.id for e in opt.optimize(b)]


# ---------- ContextAssembler ----------------------------------------------
def _ranked(uid: str, score: float, kind: str = "fn") -> RankedResult:
    return RankedResult(
        unit_id=uid,
        final_score=score,
        breakdown=RankingFeatures(),
        channels=[RetrievalChannel.VECTOR],
        kind=kind,
        qualified_name=f"pkg.m.{uid}",
        file_path="pkg/m.py",
    )


def test_assembler_maps_unit_kinds_to_priority_types() -> None:
    options = AssemblyOptions(max_context_tokens=1000)
    asm = ContextAssembler(options=options)
    ranked = [
        _ranked("m1", 0.9, kind="mod"),
        _ranked("c1", 0.8, kind="cls"),
        _ranked("f1", 0.7, kind="fn"),
        _ranked("k1", 0.6, kind="const"),
    ]
    pkt = asm.build(task="auth", ranked=ranked)

    by_id = {e.id: e for e in pkt.context}
    assert by_id["m1"].type == "architecture"
    assert by_id["c1"].type == "architecture"
    assert by_id["f1"].type == "logic"
    assert by_id["k1"].type == "code"


def test_assembler_confidence_is_mean_score() -> None:
    options = AssemblyOptions(max_context_tokens=1000)
    pkt = ContextAssembler(options=options).build(
        task="x",
        ranked=[_ranked("a", 0.4), _ranked("b", 0.6)],
    )
    assert pkt.confidence == pytest.approx(0.5)


def test_assembler_packet_passes_through_options_metadata() -> None:
    options = AssemblyOptions(
        max_context_tokens=1000,
        constraints=("must-be-async",),
        risks=("token-loop",),
        changes=("commit-abc",),
    )
    pkt = ContextAssembler(options=options).build(
        task="t", ranked=[_ranked("a", 0.5)],
    )
    assert pkt.constraints == ["must-be-async"]
    assert pkt.risks == ["token-loop"]
    assert pkt.changes == ["commit-abc"]


def test_assembler_empty_ranked_yields_empty_packet() -> None:
    pkt = ContextAssembler(options=AssemblyOptions(max_context_tokens=1000)).build(
        task="x", ranked=[],
    )
    assert pkt.context == []
    assert pkt.confidence == 0.0


def test_assembler_is_deterministic_across_runs() -> None:
    asm = ContextAssembler(options=AssemblyOptions(max_context_tokens=1000))
    ranked = [_ranked("a", 0.5, kind="fn"), _ranked("b", 0.7, kind="cls")]
    p1 = asm.build(task="t", ranked=ranked)
    p2 = asm.build(task="t", ranked=ranked)
    assert p1.model_dump_json() == p2.model_dump_json()
