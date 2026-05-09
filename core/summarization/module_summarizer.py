from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence

from core.compression.logevent import emit_phase3_event
from core.observability import get_tracer
from schemas import DenseModule, IngestionUnit, UnitKind

_tracer = get_tracer("core.summarization.module_summarizer")


def _leaf(qname: str) -> str:
    """Strip the module prefix to yield the symbol's leaf name."""
    return qname.rsplit(".", 1)[-1]


class ModuleSummarizer:
    """Per-module structural summary → DenseModule.

    Pure-structural: no LLM call, no prose. The summary lists class
    leaf names, function leaf names, top-level constants, imports,
    and source files. Same input → byte-identical DenseModule.
    """

    def summarize(self, units: Sequence[IngestionUnit]) -> list[DenseModule]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("module_summarizer.summarize") as span:
            grouped: dict[str, list[IngestionUnit]] = defaultdict(list)
            for u in units:
                # Group by module qname. Each unit's module is either
                # itself (UnitKind.MODULE) or its module ancestor; for a
                # method `pkg.m.C.x`, the module is `pkg.m`.
                module_qname = self._module_qname_of(u, units)
                grouped[module_qname].append(u)

            out: list[DenseModule] = []
            for module_qname, group in grouped.items():
                module_unit = next(
                    (g for g in group if g.kind == UnitKind.MODULE), None
                )
                if module_unit is None:
                    continue  # nothing to anchor against — skip
                cls = sorted({
                    _leaf(g.qualified_name)
                    for g in group
                    if g.kind == UnitKind.CLASS
                })
                fn = sorted({
                    _leaf(g.qualified_name)
                    for g in group
                    if g.kind == UnitKind.FUNCTION
                    and g.parent_qualified_name == module_qname
                })
                const = sorted({
                    _leaf(g.qualified_name)
                    for g in group
                    if g.kind == UnitKind.CONSTANT
                    and g.parent_qualified_name == module_qname
                })
                imp = sorted(set(module_unit.imports))
                out.append(
                    DenseModule(
                        id=module_qname,
                        cls=cls,
                        fn=fn,
                        const=const,
                        imp=imp,
                        file=[module_unit.file_path],
                    )
                )

            # Determinism: sort by id for stable iteration order.
            out.sort(key=lambda m: m.id)
            span.set_attribute("count", len(out))
            emit_phase3_event(
                event="module_summarize",
                operation="summarize",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                level="info",
                modules=len(out),
            )
            return out

    @staticmethod
    def _module_qname_of(unit: IngestionUnit, all_units: Sequence[IngestionUnit]) -> str:
        if unit.kind == UnitKind.MODULE:
            return unit.qualified_name
        # Walk parents via the in-batch index to find the enclosing module.
        index = {u.qualified_name: u for u in all_units}
        cur: IngestionUnit | None = unit
        while cur is not None and cur.parent_qualified_name is not None:
            parent = index.get(cur.parent_qualified_name)
            if parent is None:
                # Parent not in batch (e.g. methods of a foreign class).
                return cur.parent_qualified_name
            if parent.kind == UnitKind.MODULE:
                return parent.qualified_name
            cur = parent
        # No parent → unit IS its own module qname (best effort fallback).
        return unit.qualified_name
