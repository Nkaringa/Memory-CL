from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Sequence

from core.compression.logevent import emit_phase3_event
from core.observability import get_tracer
from schemas import DenseGraphSlice, GraphEdge, GraphNode, NodeKind

_tracer = get_tracer("core.summarization.graph_summarizer")


class GraphSummarizer:
    """Per-node 1-hop graph snapshot → DenseGraphSlice.

    For each non-EXTERNAL node we record direct in/out neighbors and a
    total degree. EXTERNAL nodes are skipped — they have no internal
    structure worth indexing.
    """

    def summarize(
        self,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
    ) -> list[DenseGraphSlice]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("graph_summarizer.summarize") as span:
            outgoing: dict[str, set[str]] = defaultdict(set)
            incoming: dict[str, set[str]] = defaultdict(set)
            for e in edges:
                outgoing[e.src_id].add(e.dst_id)
                incoming[e.dst_id].add(e.src_id)

            out: list[DenseGraphSlice] = []
            for n in nodes:
                if n.kind == NodeKind.EXTERNAL:
                    continue
                o = sorted(outgoing.get(n.node_id, set()))
                i = sorted(incoming.get(n.node_id, set()))
                deg = len(o) + len(i)
                out.append(
                    DenseGraphSlice(
                        id=n.node_id,
                        k=n.kind.value,
                        i=i,
                        o=o,
                        deg=deg,
                    )
                )

            out.sort(key=lambda s: s.id)
            span.set_attribute("count", len(out))
            emit_phase3_event(
                event="graph_summarize",
                operation="summarize",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                level="info",
                slices=len(out),
            )
            return out
