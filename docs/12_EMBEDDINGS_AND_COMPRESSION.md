# 12 · Embeddings + Compression

← back to [index](00_INDEX.md) · related: [09_RETRIEVAL_SYSTEM](09_RETRIEVAL_SYSTEM.md), [13_MEMORY_EVOLUTION](13_MEMORY_EVOLUTION.md), [03_DATA_FLOW](03_DATA_FLOW.md)

Phase 3 adds the **dense projection layer** between AST extraction
and storage. The goal is token efficiency without losing structural
fidelity.

## Dense schema

`schemas/dense.py::DenseRecord` — base contract per the
DENSE_NOTATION_SPEC:

- max key length: **5 chars**
- sorted keys at serialization
- arrays sorted + deduped at validation
- deterministic JSON: `sort_keys=True, separators=(",", ":")`
- empty arrays dropped from on-wire output (`drop_empty=True`)

Fields: `v` (schema version), `t` (type tag), `id`, `dep`, `api`,
`risk`, `file`, `evt`.

## Dense overlays

`schemas/compression.py` adds:

- `DenseModule` — module summary (`mod`): `cls`, `fn`, `const`, `imp`, `file`
- `DenseApi` — public-surface (`api`): `api`, `cls`, `file`
- `DenseGraphSlice` — 1-hop neighborhood (`gph`): `k`, `i`, `o`, `deg`
- `EmbeddingChunk` — text + position + optional vector (NOT dense)
- `CompressionMetrics` — counters

All schemas obey the 5-char key cap.

## Dense encoder

`core/compression/dense_encoder.py::DenseEncoder` projects a unit
into a `DenseRecord`:

- `t` derived from `UnitKind` (`mod / cls / fn / mth / const`)
- `dep` semantics depend on kind:
  - module → imports
  - class → bases
  - function/method → callees
  - constant → empty
- `file = [unit.file_path]`
- `id = qualified_name`

Bytes-input vs bytes-output recorded for the token-reduction metric.

## Summarizers

`core/summarization/`:

- `module_summarizer.py` — groups units per enclosing module → `DenseModule`
- `api_summarizer.py` — public top-level fns + classes → `DenseApi`
- `graph_summarizer.py` — per-node 1-hop slices → `DenseGraphSlice`

All are pure-structural. **No LLM call. No prose.** Phase 3 is
explicit about this — semantic summarization (LLM) is reserved for a
later phase.

## Chunking

`core/embeddings/chunking_strategy.py::ChunkingStrategy`:

- Char-based heuristic: ~4 chars/token (English code/text average).
- Inputs: `chunk_size` (tokens), `chunk_overlap` (tokens).
- Output: `EmbeddingChunk[]` with `chunk_id = "<unit_id>#cN"`.
- Deterministic — same source → same boundaries.

## Embedder

`core/embeddings/embedder.py::Embedder` is a Protocol:

```python
class Embedder(Protocol):
    name: str
    @property
    def dimension(self) -> int: ...
    async def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]: ...
```

Default implementation: `DeterministicEmbedder` — SHA-512 → 32-bit
float chunks → L2-normalized. **No PRNG. No external API.**

This is intentional:

- Tests run without network or API keys.
- Determinism guarantee holds regardless of model availability.
- Real model-backed embedders plug in at the Protocol boundary
  without changing any caller.

## Embedding pipeline

`core/embeddings/embedding_pipeline.py::EmbeddingPipeline`:

1. `vector_repo.ensure_collection(name, embedder.dimension)`
2. For each unit: `chunker.chunk_unit(unit)` → first chunk is the
   "primary" representation.
3. Batch-embed all primary chunks.
4. Build one `VectorPoint` per unit (`point_id = unit_id`); empty
   units get a payload-only point so the cross-store identity
   invariant holds.
5. `vector_repo.upsert_payloads(collection, points)`.

**One vector per unit.** Multi-chunk indexing is a Phase-11 concern;
the chunker already produces all chunks so the upgrade is local.

## Compression pipeline

`core/compression/pipeline.py::CompressionPipeline.run(ctx, units, nodes, edges)`:

1. Stage 1 — dense encode (per-unit failure isolation; failures →
   `degraded_unit_ids`).
2. Stage 2 — module + API + graph summarizers over surviving units.
3. Stage 3 — chunk + embed + upsert via `EmbeddingPipeline`.
4. Returns `CompressionResult` with all dense records, chunks, and
   metrics including a `token_reduction_ratio`.

Same input → byte-identical output. Pinned by
`test_pipeline_is_byte_deterministic_across_runs`.

## Determinism guarantees

- Dense JSON: sorted keys, compact separators (`canonical_json` in
  `core/compression/deterministic_serializer.py`).
- Chunking: `chunk_id = "<unit_id>#cN"`; boundaries are pure functions
  of length and knobs.
- Embedding: SHA-512 → fixed-length float vector → L2-normalized.

## Storage

Vectors land in Qdrant via `storage/qdrant_repo.py`:

- `point_id = unit_id` (cross-store identity).
- Phase-3 writes payload + (real vector OR zero-placeholder).
- `payload.has_vector = True` only when a real embedding exists;
  the vector retriever filters out `False` points.

---

Next: [13 — Memory Evolution](13_MEMORY_EVOLUTION.md)
