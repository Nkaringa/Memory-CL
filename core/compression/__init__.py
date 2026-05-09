from core.compression.context import CompressionContext
from core.compression.dense_encoder import DenseEncoder, EncodedUnit
from core.compression.deterministic_serializer import (
    canonical_bytes,
    canonical_json,
)
from core.compression.logevent import emit_phase3_event
from core.compression.schema_compactor import compact_payload

# `CompressionPipeline` is intentionally NOT re-exported at package level —
# it imports `core.embeddings`, which itself depends on this package's
# `logevent`, creating a cycle that fires when callers happen to import
# `core.embeddings` first. Import it explicitly:
#     from core.compression.pipeline import CompressionPipeline

__all__ = [
    "CompressionContext",
    "DenseEncoder",
    "EncodedUnit",
    "canonical_bytes",
    "canonical_json",
    "compact_payload",
    "emit_phase3_event",
]
