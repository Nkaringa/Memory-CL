"""Memory compactor — produces a CompactionPlan over low-priority units.

Per Phase-6 spec, compaction "compresses summary" rather than deletes.
We model the result as a plan: which units to merge into a per-module
aggregate summary, plus a derived DenseModule for each affected
module. The plan is consumable by an admin tool — applying it is NOT
in Phase-6 scope.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from core.lifecycle.logevent import emit_phase6_event
from core.lifecycle.relevance_scorer import RelevanceBreakdown
from core.observability import get_tracer
from core.summarization import ModuleSummarizer
from schemas import DenseModule, IngestionUnit, UnitKind

_tracer = get_tracer("core.lifecycle.memory_compactor")


@dataclass(frozen=True, slots=True)
class CompactionEntry:
    """One module's compaction result."""

    module_qname: str
    summary: DenseModule
    merged_unit_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    entries: tuple[CompactionEntry, ...]

    @property
    def merged_count(self) -> int:
        return sum(len(e.merged_unit_ids) for e in self.entries)


class MemoryCompactor:
    """Plan compaction for units whose relevance dropped below threshold.

    The compactor:
        1. Filters to UnitKind.FUNCTION / METHOD / CONSTANT under each
           module — only leaves are candidates; the module itself is
           always preserved as the aggregation anchor.
        2. Within each affected module, runs the existing Phase-3
           ModuleSummarizer over the SURVIVING units (preserving
           structure) and adds the dropped names into the summary's
           `fn` / `cls` / `const` lists so the agent can still see
           them by name even after physical compression.
    """

    def __init__(self, *, low_priority_threshold: float) -> None:
        if not 0.0 <= low_priority_threshold <= 1.0:
            raise ValueError("low_priority_threshold must be in [0, 1]")
        self._threshold = low_priority_threshold

    def plan(
        self,
        *,
        units: Sequence[IngestionUnit],
        scores: dict[str, RelevanceBreakdown],
    ) -> CompactionPlan:
        start = time.perf_counter()
        with _tracer.start_as_current_span("memory_compactor.plan") as span:
            span.set_attribute("unit_count", len(units))

            # Group surviving units per module + collect victims.
            modules: dict[str, list[IngestionUnit]] = defaultdict(list)
            victims: dict[str, list[IngestionUnit]] = defaultdict(list)
            for u in units:
                module_qname = self._module_of(u, units)
                if u.kind == UnitKind.MODULE:
                    modules[module_qname].append(u)
                    continue
                score = scores.get(u.unit_id)
                if score is not None and score.score < self._threshold and u.kind != UnitKind.CLASS:
                    victims[module_qname].append(u)
                else:
                    modules[module_qname].append(u)

            entries: list[CompactionEntry] = []
            summarizer = ModuleSummarizer()
            for module_qname in sorted(set(modules) | set(victims)):
                survivors = modules.get(module_qname, [])
                module_victims = victims.get(module_qname, [])
                if not module_victims:
                    continue  # nothing to compact in this module
                summary_list = summarizer.summarize(survivors)
                if summary_list:
                    summary = summary_list[0]
                else:
                    # No module unit in batch — synthesize an empty one.
                    summary = DenseModule(id=module_qname, file=[])
                entries.append(
                    CompactionEntry(
                        module_qname=module_qname,
                        summary=summary,
                        merged_unit_ids=tuple(
                            sorted(v.unit_id for v in module_victims)
                        ),
                    )
                )

            # Determinism: stable iteration by module qname.
            entries.sort(key=lambda e: e.module_qname)

            elapsed = (time.perf_counter() - start) * 1000
            emit_phase6_event(
                event="memory_evolution",
                entity_id="<batch>",
                operation="compact",
                relevance_score=0.0,
                status="success",
                level="info",
                latency_ms=round(elapsed, 3),
                modules_affected=len(entries),
                merged=sum(len(e.merged_unit_ids) for e in entries),
            )
            return CompactionPlan(entries=tuple(entries))

    @staticmethod
    def _module_of(
        unit: IngestionUnit, all_units: Sequence[IngestionUnit]
    ) -> str:
        if unit.kind == UnitKind.MODULE:
            return unit.qualified_name
        index = {u.qualified_name: u for u in all_units}
        cur: IngestionUnit | None = unit
        while cur is not None and cur.parent_qualified_name is not None:
            parent = index.get(cur.parent_qualified_name)
            if parent is None:
                return cur.parent_qualified_name
            if parent.kind == UnitKind.MODULE:
                return parent.qualified_name
            cur = parent
        return unit.qualified_name


__all__ = ["CompactionEntry", "CompactionPlan", "MemoryCompactor"]
