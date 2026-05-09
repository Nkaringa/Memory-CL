"""Replay engine — re-runs an operation against a captured snapshot.

The engine assumes the operation under test is *already* deterministic
(every Phase-1..7 component is). Replay is a verification tool: if
the live system produces a different output now than at snapshot
time, either the snapshot is stale (state advanced legitimately) or
the system has drifted in a non-deterministic way (a bug).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core.reproducibility.system_snapshot import SystemSnapshot


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        default=str, ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ReplayResult:
    snapshot_id: str
    matches: bool
    expected_hash: str
    actual_hash: str
    notes: str = ""
    payload: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ReplayEngine:
    """Single entry point: `replay(snapshot, operation, expected_output)`.

    `operation` is any async callable producing a JSON-serializable
    result. `expected_output` is the value captured at snapshot time;
    `replay()` re-runs `operation`, hashes both, and reports parity.
    """

    async def replay[OutT](
        self,
        snapshot: SystemSnapshot,
        operation: Callable[[], Awaitable[OutT]],
        *,
        expected_output: Any | None = None,
    ) -> ReplayResult:
        actual = await operation()
        expected_hash = (
            _stable_hash(expected_output)
            if expected_output is not None else "<not-supplied>"
        )
        actual_hash = _stable_hash(actual)
        return ReplayResult(
            snapshot_id=snapshot.snapshot_id,
            matches=expected_output is not None and expected_hash == actual_hash,
            expected_hash=expected_hash,
            actual_hash=actual_hash,
            payload=actual,
            metadata={"tenant_id": snapshot.tenant_id},
            notes=(
                "no expected output supplied; reporting actual hash only"
                if expected_output is None else ""
            ),
        )

    @staticmethod
    def equivalent_hashes(
        a: Any, b: Any,
    ) -> bool:
        """Convenience for callers that want to compare two outputs
        directly without going through the snapshot path."""
        return _stable_hash(a) == _stable_hash(b)


__all__ = ["ReplayEngine", "ReplayResult"]
