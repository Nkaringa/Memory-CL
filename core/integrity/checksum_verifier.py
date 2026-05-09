"""Re-hash and compare entity content against its stored checksum.

Detects on-disk / in-memory corruption: an `IngestionUnit`'s
`source_sha` MUST equal the SHA-256 of its `content` field. When the
verifier finds a mismatch it marks the entity as `quarantined` (a
soft Redis flag — the spec forbids deletion).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any


def _key(repo_id: str, entity_id: str) -> str:
    return f"phase8:quarantine:{repo_id}:{entity_id}"


@dataclass(frozen=True, slots=True)
class ChecksumReport:
    total: int
    matched: int
    mismatched_ids: tuple[str, ...]
    quarantined_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_violations(self) -> bool:
        return bool(self.mismatched_ids)


class Quarantine:
    """Redis-backed soft-quarantine flag.

    Mirrors the Phase-6 status pattern: writes to a single string key,
    never deletes the underlying entity. Operators can lift the flag
    explicitly via `clear`.
    """

    def __init__(self, redis_client: Any) -> None:
        self._client = redis_client

    async def mark(self, *, repo_id: str, entity_id: str, reason: str) -> None:
        await self._client.set(_key(repo_id, entity_id), reason)

    async def clear(self, *, repo_id: str, entity_id: str) -> None:
        # Best-effort delete — falls back to setting an empty string
        # for clients that don't expose `delete`.
        delete = getattr(self._client, "delete", None)
        if delete is not None:
            await delete(_key(repo_id, entity_id))
        else:
            await self._client.set(_key(repo_id, entity_id), "")

    async def is_quarantined(self, *, repo_id: str, entity_id: str) -> bool:
        raw = await self._client.get(_key(repo_id, entity_id))
        return bool(raw)


class ChecksumVerifier:
    """Pure verifier — no I/O on its own.

    `verify_units` takes any iterable of objects exposing
    `unit_id`, `content`, `source_sha`. The caller decides whether
    to call `Quarantine.mark` on the resulting `mismatched_ids`.
    """

    def verify_units(self, units: Iterable[Any]) -> ChecksumReport:
        mismatched: list[str] = []
        total = 0
        matched = 0
        for u in units:
            total += 1
            content = getattr(u, "content", "")
            stored = getattr(u, "source_sha", "")
            actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if actual == stored:
                matched += 1
            else:
                mismatched.append(getattr(u, "unit_id", ""))
        mismatched.sort()
        return ChecksumReport(
            total=total, matched=matched, mismatched_ids=tuple(mismatched),
        )

    async def quarantine_mismatches(
        self, *, repo_id: str, units: Sequence[Any], quarantine: Quarantine,
    ) -> ChecksumReport:
        report = self.verify_units(units)
        if not report.mismatched_ids:
            return report
        for uid in report.mismatched_ids:
            await quarantine.mark(
                repo_id=repo_id, entity_id=uid,
                reason="checksum_mismatch",
            )
        return ChecksumReport(
            total=report.total,
            matched=report.matched,
            mismatched_ids=report.mismatched_ids,
            quarantined_ids=report.mismatched_ids,
        )


__all__ = ["ChecksumReport", "ChecksumVerifier", "Quarantine"]
