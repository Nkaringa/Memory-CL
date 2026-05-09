"""Cross-phase schema-version compatibility check.

Each `VersionedModel`-derived schema carries `schema_version`. Phase 8
verifies that every entity surfaced from storage matches the version
the running code expects — bumping the version without a migration
path leaves stale rows that retrieval would silently mis-decode.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from schemas import SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class SchemaCompatibility:
    expected_version: str
    total_checked: int
    incompatible_ids: tuple[str, ...] = field(default_factory=tuple)
    incompatible_versions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.incompatible_ids


class SchemaValidator:
    """Pure version-comparison helper.

    Phase 8 ships strict-equality checking; backward-compatible
    semver checks are a Phase-9 concern that wouldn't change this
    helper's signature.
    """

    def __init__(self, *, expected_version: str = SCHEMA_VERSION) -> None:
        self._expected = expected_version

    @property
    def expected_version(self) -> str:
        return self._expected

    def validate(self, entities: Iterable[object]) -> SchemaCompatibility:
        ids: list[str] = []
        versions: set[str] = set()
        total = 0
        for e in entities:
            total += 1
            version = getattr(e, "schema_version", None)
            if version != self._expected:
                ids.append(str(getattr(e, "unit_id", getattr(e, "id", ""))))
                if version is not None:
                    versions.add(str(version))
        ids.sort()
        return SchemaCompatibility(
            expected_version=self._expected,
            total_checked=total,
            incompatible_ids=tuple(ids),
            incompatible_versions=tuple(sorted(versions)),
        )


__all__ = ["SchemaCompatibility", "SchemaValidator"]
