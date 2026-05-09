from __future__ import annotations

import time
from collections.abc import Sequence

from core.compression.logevent import emit_phase3_event
from core.observability import get_tracer
from schemas import DenseApi, IngestionUnit, UnitKind

_tracer = get_tracer("core.summarization.api_summarizer")


def _is_public(name: str) -> bool:
    """Public-symbol heuristic: not starting with `_`, not a dunder."""
    return not name.startswith("_")


class ApiSummarizer:
    """Extract the public API surface per module → DenseApi.

    What counts as "public": top-level functions and classes whose leaf
    name does not start with an underscore. Methods are NOT included
    here — class-internal API lives inside the class node.
    """

    def summarize(self, units: Sequence[IngestionUnit]) -> list[DenseApi]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("api_summarizer.summarize") as span:
            modules = {u.qualified_name: u for u in units if u.kind == UnitKind.MODULE}
            by_module_api: dict[str, set[str]] = {q: set() for q in modules}
            by_module_cls: dict[str, set[str]] = {q: set() for q in modules}

            for u in units:
                if u.parent_qualified_name not in modules:
                    continue
                if not _is_public(u.name):
                    continue
                if u.kind == UnitKind.FUNCTION:
                    by_module_api[u.parent_qualified_name].add(u.name)
                elif u.kind == UnitKind.CLASS:
                    by_module_cls[u.parent_qualified_name].add(u.name)

            out: list[DenseApi] = []
            for qname, module_unit in modules.items():
                api_names = by_module_api.get(qname, set())
                cls_names = by_module_cls.get(qname, set())
                if not api_names and not cls_names:
                    continue
                out.append(
                    DenseApi(
                        id=qname,
                        api=sorted(api_names),
                        cls=sorted(cls_names),
                        file=[module_unit.file_path],
                    )
                )

            out.sort(key=lambda r: r.id)
            span.set_attribute("count", len(out))
            emit_phase3_event(
                event="api_summarize",
                operation="summarize",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                level="info",
                apis=len(out),
            )
            return out
