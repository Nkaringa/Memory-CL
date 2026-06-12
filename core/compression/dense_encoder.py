from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from core.compression.logevent import emit_phase3_event
from core.observability import get_tracer
from schemas import DenseRecord, IngestionUnit, UnitKind

_tracer = get_tracer("core.compression.dense_encoder")


# Maps the parser's UnitKind to the dense `t` tag. Phase-2 UnitKind
# values (`mod`, `cls`, `fn`, `mth`, `const`) already satisfy the
# DENSE_NOTATION_SPEC max-key length, so we forward them verbatim.
_KIND_TO_T: dict[UnitKind, str] = {
    UnitKind.MODULE: "mod",
    UnitKind.CLASS: "cls",
    UnitKind.FUNCTION: "fn",
    UnitKind.METHOD: "mth",
    UnitKind.CONSTANT: "const",
    UnitKind.SECTION: "sec",
}


@dataclass(frozen=True, slots=True)
class EncodedUnit:
    """Pairing of the original unit with its dense projection.

    The pipeline keeps the original around so downstream stages
    (chunking, embedding) can read full content without re-querying
    Postgres.
    """

    unit_id: str
    record: DenseRecord
    bytes_input: int
    bytes_output: int


class DenseEncoder:
    """Project IngestionUnits into DenseRecords (Phase-2 dense schema).

    No new ontology types are introduced — only existing DenseRecord
    fields (`v/t/id/dep/api/risk/file/evt`) are populated. Specialized
    summary records (DenseModule etc.) live in the summarization layer.
    """

    def encode_unit(self, unit: IngestionUnit) -> EncodedUnit:
        start = time.perf_counter()
        with _tracer.start_as_current_span("dense_encoder.encode_unit") as span:
            span.set_attribute("unit_id", unit.unit_id)
            span.set_attribute("kind", unit.kind.value)

            t = _KIND_TO_T[unit.kind]
            # `dep` semantics depend on kind:
            #   - module: imports
            #   - function/method: callees (no leading dunders)
            #   - class: bases
            #   - constant: empty
            if unit.kind == UnitKind.MODULE:
                dep = list(unit.imports)
            elif unit.kind in (UnitKind.FUNCTION, UnitKind.METHOD):
                dep = list(unit.calls)
            elif unit.kind == UnitKind.CLASS:
                dep = list(unit.bases)
            else:
                dep = []

            record = DenseRecord(
                t=t,
                id=unit.qualified_name,
                dep=dep,
                file=[unit.file_path],
            )

            in_bytes = len(unit.content.encode("utf-8"))
            out_bytes = len(record.to_dense_json(drop_empty=True).encode("utf-8"))
            ratio = 1.0 - (out_bytes / in_bytes) if in_bytes else 0.0
            span.set_attribute("bytes_input", in_bytes)
            span.set_attribute("bytes_output", out_bytes)

            emit_phase3_event(
                event="dense_encode",
                operation="encode",
                status="success",
                duration_ms=(time.perf_counter() - start) * 1000,
                unit_id=unit.unit_id,
                token_reduction_ratio=ratio,
                level="debug",
                kind=unit.kind.value,
            )
            return EncodedUnit(
                unit_id=unit.unit_id,
                record=record,
                bytes_input=in_bytes,
                bytes_output=out_bytes,
            )

    def encode_units(self, units: Sequence[IngestionUnit]) -> list[EncodedUnit]:
        # Deterministic ordering (already guaranteed by the parser, but
        # re-asserted here so a future caller passing unsorted input
        # still gets stable downstream artifacts).
        ordered = sorted(units, key=lambda u: u.unit_id)
        return [self.encode_unit(u) for u in ordered]
