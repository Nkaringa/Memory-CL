"""Hash-chained, append-only log store.

Each entry's `hash` covers `prev_hash + canonical_json(payload)`. A
later entry depends on every prior entry's hash, so any tampering
breaks the chain at the point of modification — `verify_chain()`
walks the log and reports the first broken link.

The store is in-memory by default; persistence is the job of the
sink (`infra/audit/audit_sink.py`), which can serialize entries to
JSONL or any other backend.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

# Genesis-block hash. 64 zeros is unmistakable in a chain dump.
GENESIS_HASH: str = "0" * 64


def _canonical_json(payload: Any) -> str:
    """Same canonical encoding Phase 3 uses, kept local to avoid an
    upward import from `core/`. Sorted keys + compact separators."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _link_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """SHA-256 over (prev_hash || canonical_json(payload))."""
    h = hashlib.sha256()
    h.update(prev_hash.encode("ascii"))
    h.update(b"\x00")
    h.update(_canonical_json(payload).encode("utf-8"))
    return h.hexdigest()


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One link in the chain."""

    seq: int
    prev_hash: str
    hash: str
    payload: dict[str, Any] = field(default_factory=dict)


class ChainBrokenError(RuntimeError):
    """Raised when `verify_chain()` finds tampering."""

    def __init__(self, seq: int, expected: str, actual: str) -> None:
        self.seq = seq
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"chain broken at seq={seq}: expected hash={expected[:12]}…, "
            f"actual={actual[:12]}…"
        )


class ImmutableLogStore:
    """Append-only, hash-chained log.

    Operations:
        append(payload)          → LogEntry
        verify_chain()           → True / raise ChainBrokenError
        head() / tail() / __iter__()

    The store is thread-safe for concurrent appends — every append
    holds a single lock, so the order assigned to `seq` is
    deterministic w.r.t. the sequence of acquired-lock moments.
    """

    def __init__(self, *, genesis_hash: str = GENESIS_HASH) -> None:
        self._entries: list[LogEntry] = []
        self._lock = threading.Lock()
        self._genesis = genesis_hash

    # ----- writes -----
    def append(self, payload: dict[str, Any]) -> LogEntry:
        with self._lock:
            prev_hash = self._entries[-1].hash if self._entries else self._genesis
            seq = len(self._entries)
            link_hash = _link_hash(prev_hash, payload)
            entry = LogEntry(
                seq=seq, prev_hash=prev_hash, hash=link_hash, payload=dict(payload),
            )
            self._entries.append(entry)
            return entry

    # ----- reads -----
    def __iter__(self) -> Iterator[LogEntry]:
        # Snapshot copy to avoid leaking the live list to mutating callers.
        return iter(list(self._entries))

    def __len__(self) -> int:
        return len(self._entries)

    def head(self) -> LogEntry | None:
        return self._entries[0] if self._entries else None

    def tail(self) -> LogEntry | None:
        return self._entries[-1] if self._entries else None

    def get(self, seq: int) -> LogEntry:
        if seq < 0 or seq >= len(self._entries):
            raise IndexError(f"seq {seq} out of range")
        return self._entries[seq]

    # ----- integrity -----
    def verify_chain(self) -> bool:
        prev = self._genesis
        for e in self._entries:
            expected = _link_hash(prev, e.payload)
            if e.prev_hash != prev:
                raise ChainBrokenError(seq=e.seq, expected=prev, actual=e.prev_hash)
            if e.hash != expected:
                raise ChainBrokenError(seq=e.seq, expected=expected, actual=e.hash)
            prev = e.hash
        return True

    def to_jsonl(self) -> str:
        """Dump the whole chain as JSONL (one entry per line) — useful
        for shipping to an external sink without losing any field."""
        return "\n".join(_canonical_json(asdict(e)) for e in self._entries)


__all__ = ["GENESIS_HASH", "ChainBrokenError", "ImmutableLogStore", "LogEntry"]
