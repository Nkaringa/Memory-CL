"""Corruption detector — combines checksum + integrity reports.

A single `detect(...)` call rolls up:
    * `ChecksumReport.mismatched_ids`
    * `GraphIntegrityReport.violations`
    * `SchemaCompatibility.incompatible_ids`
    * audit chain integrity (`ImmutableLogStore.verify_chain()`)

into a `CorruptionReport` an operator can act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.integrity.checksum_verifier import ChecksumReport
from core.integrity.graph_validator import GraphIntegrityReport
from core.integrity.schema_validator import SchemaCompatibility
from infra.audit.immutable_log_store import ChainBrokenError, ImmutableLogStore


@dataclass(frozen=True, slots=True)
class CorruptionReport:
    audit_chain_intact: bool
    checksum_mismatches: int
    graph_violations: int
    schema_incompatibilities: int
    affected_entity_ids: tuple[str, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_corruption(self) -> bool:
        return (
            not self.audit_chain_intact
            or self.checksum_mismatches > 0
            or self.graph_violations > 0
            or self.schema_incompatibilities > 0
        )


class CorruptionDetector:
    def detect(
        self,
        *,
        checksum: ChecksumReport,
        graph: GraphIntegrityReport,
        schema: SchemaCompatibility,
        audit_log: ImmutableLogStore | None = None,
    ) -> CorruptionReport:
        notes: list[str] = []
        chain_ok = True
        if audit_log is not None:
            try:
                audit_log.verify_chain()
            except ChainBrokenError as exc:
                chain_ok = False
                notes.append(f"audit chain broken at seq={exc.seq}")

        affected: set[str] = set()
        affected.update(checksum.mismatched_ids)
        affected.update(schema.incompatible_ids)
        for v in graph.violations:
            if v.node_id:
                affected.add(v.node_id)
            if v.src_id:
                affected.add(v.src_id)
            if v.dst_id:
                affected.add(v.dst_id)

        return CorruptionReport(
            audit_chain_intact=chain_ok,
            checksum_mismatches=len(checksum.mismatched_ids),
            graph_violations=len(graph.violations),
            schema_incompatibilities=len(schema.incompatible_ids),
            affected_entity_ids=tuple(sorted(affected)),
            notes=tuple(notes),
        )


__all__ = ["CorruptionDetector", "CorruptionReport"]
