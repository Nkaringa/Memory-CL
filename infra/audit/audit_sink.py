"""Audit sinks — destinations for emitted audit events.

Two implementations:
    * `InMemoryAuditSink` — used by tests + ephemeral processes
    * `JsonlFileAuditSink` — append-only JSONL on disk, persistent

Both sit behind a `Protocol` so the audit logger can swap them out
without changing call sites.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from infra.audit.immutable_log_store import ImmutableLogStore, LogEntry


@runtime_checkable
class AuditSink(Protocol):
    """Sink contract — every implementation appends in order."""

    def write(self, entry: LogEntry) -> None: ...

    def flush(self) -> None: ...


class InMemoryAuditSink:
    """Stores entries in a list. Thread-safe for the simple cases."""

    def __init__(self) -> None:
        self._entries: list[LogEntry] = []
        self._lock = threading.Lock()

    def write(self, entry: LogEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def flush(self) -> None:
        return None

    @property
    def entries(self) -> Sequence[LogEntry]:
        with self._lock:
            return list(self._entries)


class JsonlFileAuditSink:
    """Appends entries to a JSONL file.

    Designed for low-volume durable audit storage. Each `write` opens
    the file in append mode + flushes — slow but the integrity
    guarantee matters more than throughput here.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, entry: LogEntry) -> None:
        line = json.dumps(
            {
                "seq": entry.seq,
                "prev_hash": entry.prev_hash,
                "hash": entry.hash,
                "payload": entry.payload,
            },
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def flush(self) -> None:
        return None

    def replay(self) -> ImmutableLogStore:
        """Reconstruct an `ImmutableLogStore` from the file's contents.

        Verification is the caller's responsibility (`store.verify_chain()`)
        — this is just deserialization.
        """
        store = ImmutableLogStore()
        if not self._path.exists():
            return store
        with self._path.open("r", encoding="utf-8") as f:
            for raw in f:
                if not raw.strip():
                    continue
                rec = json.loads(raw)
                # Re-append by writing the payload — the store reapplies
                # the chain rule and sequencing on its own.
                store.append(rec["payload"])
        return store


def write_many(sink: AuditSink, entries: Iterable[LogEntry]) -> int:
    """Best-effort multi-write helper used by replays + bulk imports."""
    count = 0
    for entry in entries:
        sink.write(entry)
        count += 1
    sink.flush()
    return count


__all__ = [
    "AuditSink",
    "InMemoryAuditSink",
    "JsonlFileAuditSink",
    "write_many",
]
