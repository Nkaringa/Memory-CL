"""Hash-chained audit logger emitting the Phase-8 `audit_event` shape.

Every system action calls `AuditLogger.record(...)` which:
    1. computes `before_hash` / `after_hash` (caller-supplied or
       freshly hashed from the entity's state)
    2. assembles the spec-mandated payload (actor, action,
       entity_id, tenant_id, timestamp, …)
    3. appends to the `ImmutableLogStore` (hash-chained)
    4. writes to the configured `AuditSink`
    5. emits a structlog `audit_event` for live observability

The logger is the only allowed producer of audit events — every
other Phase-8 module routes through it.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from core.logging import get_logger
from infra.audit.audit_sink import AuditSink, InMemoryAuditSink
from infra.audit.immutable_log_store import ImmutableLogStore, LogEntry

_log = get_logger("phase_8")


class AuditActor(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class AuditAction(StrEnum):
    INGEST = "ingest"
    RETRIEVE = "retrieve"
    UPDATE = "update"
    ROUTE = "route"
    RANK = "rank"
    POLICY_DECIDE = "policy_decide"
    QUARANTINE = "quarantine"
    SNAPSHOT = "snapshot"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Spec-mandated fields, plus a few useful extras under `metadata`.

    `before_hash` / `after_hash` capture the entity's content fingerprint
    before and after the action — they're the property that lets a
    later replay verify "the action mutated this exact byte stream".
    """

    actor: AuditActor
    action: AuditAction
    entity_id: str
    before_hash: str
    after_hash: str
    tenant_id: str
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """Canonical wire payload — sorted keys at serialization time."""
        return {
            "event": "audit_event",
            "phase": "phase_8",
            "actor": self.actor.value,
            "action": self.action.value,
            "entity_id": self.entity_id,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp.isoformat(),
            "metadata": dict(sorted(self.metadata.items())),
        }


def hash_state(state: Any) -> str:
    """Deterministic SHA-256 over an arbitrary state object.

    Used by callers that want to compute before/after hashes without
    importing the canonical-bytes helper from `core/compression/`.
    """
    if state is None:
        return "0" * 64
    if isinstance(state, (bytes, bytearray)):
        return hashlib.sha256(bytes(state)).hexdigest()
    if isinstance(state, str):
        return hashlib.sha256(state.encode("utf-8")).hexdigest()
    # Fall through to canonical JSON for dicts / dataclasses / models.
    import json as _json
    try:
        encoded = _json.dumps(state, sort_keys=True, separators=(",", ":"),
                              default=str, ensure_ascii=False).encode("utf-8")
    except TypeError:
        encoded = repr(state).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AuditLogger:
    """Process-wide audit emitter."""

    def __init__(
        self,
        *,
        store: ImmutableLogStore | None = None,
        sink: AuditSink | None = None,
    ) -> None:
        self._store = store or ImmutableLogStore()
        self._sink = sink or InMemoryAuditSink()
        self._lock = threading.Lock()

    @property
    def store(self) -> ImmutableLogStore:
        return self._store

    @property
    def sink(self) -> AuditSink:
        return self._sink

    def record(
        self,
        *,
        actor: AuditActor | str,
        action: AuditAction | str,
        entity_id: str,
        tenant_id: str,
        before: Any = None,
        after: Any = None,
        before_hash: str | None = None,
        after_hash: str | None = None,
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        level: Literal["debug", "info", "warning", "error"] = "info",
    ) -> LogEntry:
        """Emit an `audit_event` and return the chain entry it produced."""
        evt = AuditEvent(
            actor=AuditActor(actor) if isinstance(actor, str) else actor,
            action=AuditAction(action) if isinstance(action, str) else action,
            entity_id=entity_id,
            tenant_id=tenant_id,
            before_hash=before_hash if before_hash is not None else hash_state(before),
            after_hash=after_hash if after_hash is not None else hash_state(after),
            timestamp=timestamp or datetime.now(UTC),
            metadata=dict(metadata or {}),
        )
        payload = evt.to_payload()
        with self._lock:
            entry = self._store.append(payload)
        # Sink + structlog emission outside the chain lock — they can
        # be slower than the chain step without serializing writers.
        self._sink.write(entry)
        log_method = getattr(_log, level)
        log_method(
            "audit_event",
            seq=entry.seq,
            hash=entry.hash[:16],
            **{k: v for k, v in payload.items() if k != "event"},
        )
        return entry

    def verify(self) -> bool:
        """Re-walk the chain to catch tampering. Raises on first break."""
        return self._store.verify_chain()

    def replay_into(self, other: AuditLogger) -> int:
        """Re-append every event from this logger into `other`.

        Used by the replay engine — the destination logger's chain
        will be built from scratch with deterministic hash linkage.
        """
        copied = 0
        for entry in self._store:
            other.record(
                actor=entry.payload["actor"],
                action=entry.payload["action"],
                entity_id=entry.payload["entity_id"],
                tenant_id=entry.payload["tenant_id"],
                before_hash=entry.payload["before_hash"],
                after_hash=entry.payload["after_hash"],
                timestamp=datetime.fromisoformat(entry.payload["timestamp"]),
                metadata=entry.payload.get("metadata", {}),
                level="debug",
            )
            copied += 1
        return copied


def collect_payloads(entries: Iterable[LogEntry]) -> list[dict[str, Any]]:
    """Project an entry stream to its payload dicts. Tests use this
    to make assertions against the audit trail without juggling
    LogEntry internals."""
    return [dict(e.payload) for e in entries]


__all__ = [
    "AuditAction",
    "AuditActor",
    "AuditEvent",
    "AuditLogger",
    "collect_payloads",
    "hash_state",
]
