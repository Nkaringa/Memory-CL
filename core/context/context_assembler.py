from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from core.context.context_optimizer import ContextOptimizer
from core.observability import get_tracer
from core.retrieval.logevent import emit_phase4_event
from schemas import (
    ContextEntry,
    ContextEntryType,
    ContextPacket,
    RankedResult,
    UnitKind,
)

_tracer = get_tracer("core.context.context_assembler")


# UnitKind → ContextEntryType. Phase-4 ships a structural mapping; later
# phases that infer "constraint" / "risk" semantically will plug in via
# `unit_type_overrides` rather than rewriting this table.
_DEFAULT_UNIT_TYPE_MAP: dict[str, ContextEntryType] = {
    UnitKind.MODULE.value: "architecture",
    UnitKind.CLASS.value: "architecture",
    UnitKind.FUNCTION.value: "logic",
    UnitKind.METHOD.value: "logic",
    UnitKind.CONSTANT.value: "code",
    # Markdown sections describe intent/design — architecture tier.
    UnitKind.SECTION.value: "architecture",
}


@dataclass(frozen=True, slots=True)
class AssemblyOptions:
    """Knobs for ContextAssembler.

    Keeping these on a frozen dataclass (rather than threading kwargs
    through every method) means the API endpoint can construct one
    instance from `Settings` and reuse it across requests.
    """

    max_context_tokens: int
    constraints: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    changes: tuple[str, ...] = ()


class ContextAssembler:
    """Convert ranked results into the spec'd `ContextPacket`.

    Pipeline:
        1. Map each `RankedResult` to a `ContextEntry` using the kind →
           ContextEntryType table.
        2. Deduplicate + budget-trim via `ContextOptimizer` (priority
           order: constraint > risk > architecture > logic > code).
        3. Compute `confidence` as the mean final_score of the surviving
           entries (clamped to [0, 1]).
    """

    def __init__(
        self,
        *,
        options: AssemblyOptions,
        unit_type_overrides: dict[str, ContextEntryType] | None = None,
    ) -> None:
        self._options = options
        self._optimizer = ContextOptimizer(max_tokens=options.max_context_tokens)
        self._kind_map = {
            **_DEFAULT_UNIT_TYPE_MAP,
            **(unit_type_overrides or {}),
        }

    def build(
        self,
        *,
        task: str,
        ranked: Sequence[RankedResult],
        query_id: str = "",
        repo_id: str = "",
    ) -> ContextPacket:
        start = time.perf_counter()
        with _tracer.start_as_current_span("context_assembler.build") as span:
            span.set_attribute("query_id", query_id)
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("ranked_count", len(ranked))

            entries = [self._to_entry(r) for r in ranked]
            optimized = self._optimizer.optimize(entries)
            confidence = self._confidence(optimized)

            packet = ContextPacket(
                task=task,
                context=optimized,
                risks=list(self._options.risks),
                constraints=list(self._options.constraints),
                changes=list(self._options.changes),
                confidence=confidence,
            )

            elapsed = (time.perf_counter() - start) * 1000
            span.set_attribute("packet_entries", len(optimized))
            span.set_attribute("confidence", confidence)
            emit_phase4_event(
                event="context_assembled",
                operation="assemble",
                status="success",
                latency_ms=elapsed,
                query_id=query_id,
                repo_id=repo_id,
                level="info",
                in_count=len(ranked),
                out_count=len(optimized),
                confidence=round(confidence, 6),
            )
            return packet

    def _to_entry(self, r: RankedResult) -> ContextEntry:
        entry_type = self._kind_map.get(r.kind or "", "code")
        # `data` carries the minimum a downstream agent needs to find
        # the source — rich data lives in the dense records the retrieval
        # API returns alongside the packet (Phase 5+).
        data: dict[str, object] = {
            "qualified_name": r.qualified_name or "",
            "file_path": r.file_path or "",
            "kind": r.kind or "",
            "channels": [c.value for c in r.channels],
        }
        return ContextEntry(
            id=r.unit_id, type=entry_type, score=r.final_score, data=data,
        )

    @staticmethod
    def _confidence(entries: Sequence[ContextEntry]) -> float:
        if not entries:
            return 0.0
        avg = sum(e.score for e in entries) / len(entries)
        return max(0.0, min(1.0, avg))


__all__ = ["AssemblyOptions", "ContextAssembler"]
