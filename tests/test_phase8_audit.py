from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.governance import AuditAction, AuditActor, AuditLogger, hash_state
from infra.audit import (
    InMemoryAuditSink,
    JsonlFileAuditSink,
)
from infra.audit.immutable_log_store import (
    GENESIS_HASH,
    ChainBrokenError,
    ImmutableLogStore,
    LogEntry,
)


# ---- ImmutableLogStore ----------------------------------------------------
def test_first_entry_links_to_genesis_hash() -> None:
    store = ImmutableLogStore()
    entry = store.append({"k": "v"})
    assert entry.seq == 0
    assert entry.prev_hash == GENESIS_HASH


def test_chain_links_consecutive_entries() -> None:
    store = ImmutableLogStore()
    a = store.append({"x": 1})
    b = store.append({"x": 2})
    assert b.prev_hash == a.hash


def test_verify_chain_passes_for_clean_chain() -> None:
    store = ImmutableLogStore()
    for i in range(5):
        store.append({"i": i})
    assert store.verify_chain() is True


def test_verify_chain_detects_payload_tampering() -> None:
    store = ImmutableLogStore()
    store.append({"i": 0})
    store.append({"i": 1})
    # Forcibly mutate an entry's payload — the chain hash no longer matches.
    tampered = LogEntry(
        seq=0,
        prev_hash=GENESIS_HASH,
        hash=store.get(0).hash,
        payload={"i": 999},  # ← changed
    )
    store._entries[0] = tampered  # type: ignore[attr-defined]
    with pytest.raises(ChainBrokenError):
        store.verify_chain()


def test_canonical_payload_invariant_under_key_reorder() -> None:
    """Two payloads with different insertion orders but identical
    sorted-key serialization MUST hash to the same value."""
    a = ImmutableLogStore()
    b = ImmutableLogStore()
    a.append({"a": 1, "b": 2})
    b.append({"b": 2, "a": 1})
    assert a.get(0).hash == b.get(0).hash


def test_jsonl_dump_round_trips_through_sink(tmp_path: Path) -> None:
    sink = JsonlFileAuditSink(tmp_path / "audit.jsonl")
    store = ImmutableLogStore()
    for i in range(3):
        sink.write(store.append({"i": i}))
    rebuilt = sink.replay()
    assert len(rebuilt) == 3
    assert rebuilt.verify_chain() is True


# ---- AuditLogger ----------------------------------------------------------
def test_audit_logger_emits_spec_mandated_payload() -> None:
    logger = AuditLogger()
    entry = logger.record(
        actor=AuditActor.AGENT,
        action=AuditAction.RETRIEVE,
        entity_id="u1",
        tenant_id="acme",
        before="state-before",
        after="state-after",
    )
    payload = entry.payload
    # Required spec keys present.
    for k in ("event", "phase", "actor", "action", "entity_id",
              "before_hash", "after_hash", "tenant_id", "timestamp"):
        assert k in payload, k
    assert payload["phase"] == "phase_8"
    assert payload["event"] == "audit_event"
    # Hashes are computed from `before` / `after` if not supplied.
    assert payload["before_hash"] == hash_state("state-before")
    assert payload["after_hash"] == hash_state("state-after")


def test_audit_logger_chain_remains_verifiable_after_many_records() -> None:
    logger = AuditLogger()
    for i in range(10):
        logger.record(
            actor=AuditActor.SYSTEM,
            action=AuditAction.UPDATE,
            entity_id=f"u{i}",
            tenant_id="acme",
            before=str(i),
            after=str(i + 1),
        )
    assert logger.verify() is True


def test_audit_logger_writes_to_sink() -> None:
    sink = InMemoryAuditSink()
    logger = AuditLogger(sink=sink)
    logger.record(
        actor=AuditActor.USER, action=AuditAction.INGEST,
        entity_id="u1", tenant_id="t",
    )
    logger.record(
        actor=AuditActor.AGENT, action=AuditAction.RETRIEVE,
        entity_id="u2", tenant_id="t",
    )
    assert len(sink.entries) == 2


def test_audit_logger_replay_into_produces_equivalent_chain() -> None:
    src = AuditLogger()
    when = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(3):
        src.record(
            actor=AuditActor.SYSTEM, action=AuditAction.UPDATE,
            entity_id=f"u{i}", tenant_id="t",
            before_hash=f"h{i}", after_hash=f"h{i+1}",
            timestamp=when,
        )
    dst = AuditLogger()
    copied = src.replay_into(dst)
    assert copied == 3
    assert dst.verify() is True
    assert len(dst.store) == 3


def test_hash_state_deterministic_for_dicts_with_unsorted_keys() -> None:
    a = hash_state({"a": 1, "b": 2})
    b = hash_state({"b": 2, "a": 1})
    assert a == b
