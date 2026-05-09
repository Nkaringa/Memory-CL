"""Cross-store consistency reporter.

Verifies that the three Phase-2 stores agree on entity identity:
    * Postgres has unit_id X
    * Neo4j has node_id == X
    * Qdrant has point_id == X

Reports any one-store-only or two-store-only entities. Pure read.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    postgres_only: tuple[str, ...]
    neo4j_only: tuple[str, ...]
    qdrant_only: tuple[str, ...]
    in_all_three: int
    total_entities: int
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def fully_consistent(self) -> bool:
        return not (self.postgres_only or self.neo4j_only or self.qdrant_only)


class ConsistencyReporter:
    """Pure set-comparison helper. Inputs are id sets per store."""

    def report(
        self,
        *,
        postgres_ids: Iterable[str],
        neo4j_ids: Iterable[str],
        qdrant_ids: Iterable[str],
    ) -> ConsistencyReport:
        pg = set(postgres_ids)
        nj = set(neo4j_ids)
        qd = set(qdrant_ids)

        in_all = pg & nj & qd
        # Unique to a single store = (store) - (any other two).
        pg_only = pg - (nj | qd)
        nj_only = nj - (pg | qd)
        qd_only = qd - (pg | nj)
        all_ids = pg | nj | qd

        notes: list[str] = []
        if pg_only:
            notes.append(f"{len(pg_only)} entities only in Postgres")
        if nj_only:
            notes.append(f"{len(nj_only)} entities only in Neo4j")
        if qd_only:
            notes.append(f"{len(qd_only)} entities only in Qdrant")

        return ConsistencyReport(
            postgres_only=tuple(sorted(pg_only)),
            neo4j_only=tuple(sorted(nj_only)),
            qdrant_only=tuple(sorted(qd_only)),
            in_all_three=len(in_all),
            total_entities=len(all_ids),
            notes=tuple(notes),
        )


__all__ = ["ConsistencyReport", "ConsistencyReporter"]
