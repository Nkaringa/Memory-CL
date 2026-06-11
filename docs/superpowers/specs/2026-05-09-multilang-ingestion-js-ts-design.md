# Multi-language ingestion — JS/TS via tree-sitter

**Date:** 2026-05-09
**Branch:** `feat/multilang-ingestion-js-ts`
**Status:** Approved design

## Problem

Memory-CL ingestion is Python-only. `FileWalker` walks every file but only
`.py` gets parsed; everything else is silently skipped. Real-world repos are
polyglot — the JA4M test repo has 132 `.cjs` + 29 `.js` files that produce
zero graph nodes/edges today. This is Gap 2 in the project backlog.

## Goal

Parse JavaScript and TypeScript (including JSX/TSX) into `IngestionUnit`s
with **full parity** to the Python parser: units (module / class / function /
method / constant) plus imports, calls, references, and inheritance — so
`query_graph`, `get_related_components`, and `get_context` work identically
on JS/TS code.

Non-goals (explicitly out of scope for this iteration):

- HTML / CSS parsing (Tier-3 in backlog; symbol-extraction-only value)
- Go / Java / Rust (next tiers; this design makes them additive)
- Cross-language edge resolution (a TS file importing a Python API stays two
  separate subgraphs)
- Phase-3 embeddings (separate gap)

## Approach decision

Chosen: **tree-sitter** via the `tree-sitter` Python bindings plus the
`tree-sitter-javascript` and `tree-sitter-typescript` grammar packages.

Rejected alternatives:

- **Native toolchain subprocess** (Node + @babel/parser or TS compiler API):
  highest fidelity but puts Node.js inside the api image, adds subprocess
  lifecycle handling, and doesn't generalize (Go would need the Go
  toolchain, Java a JVM).
- **Regex/heuristic extraction**: zero deps but breaks constantly on real
  JS/TS syntax (arrow functions, destructured exports, JSX). Graph quality
  too low to trust.

Why tree-sitter: pre-built wheels (pure `pip install`, no Node), one engine
class reused for every future language (a new language = grammar package +
a rules table), industry-standard (GitHub code navigation), incremental and
error-tolerant parsing (a syntax error doesn't kill the whole file).

## Architecture

### Touch points (existing code)

| File | Change |
|---|---|
| `schemas/ingest.py` | `Language` enum gains `JAVASCRIPT = "javascript"`, `TYPESCRIPT = "typescript"` |
| `core/parsing/file_walker.py` | `LANGUAGE_EXTENSIONS` gains the JS/TS extension rows; `.d.ts` excluded |
| `core/parsing/treesitter_parser.py` | **NEW** — engine + per-language extraction rules |
| `core/parsing/__init__.py` | export new parser |
| `core/ingestion/pipeline.py` | replace hardcoded `PythonParser` with a `dict[Language, Parser]` registry |
| `requirements.lock.txt` | add pinned `tree-sitter`, `tree-sitter-javascript`, `tree-sitter-typescript` |

`IngestionUnit`, the graph builder, and all storage layers are untouched —
the unit schema is already language-agnostic.

### Extension → language mapping

| Extensions | Language |
|---|---|
| `.js` `.mjs` `.cjs` `.jsx` | JAVASCRIPT |
| `.ts` `.tsx` `.mts` `.cts` | TYPESCRIPT |
| `.d.ts` (and `.d.mts`/`.d.cts`) | skipped — type declarations carry no logic |

The walker keys on `path.suffix`, which for `foo.d.ts` is `.ts` — the `.d.*`
exclusion therefore needs an explicit name-based check, not just the suffix
table.

### Parser registry (pipeline dispatch)

```python
# pipeline.py (shape, not literal code)
self._parsers: dict[Language, SourceParser] = {
    Language.PYTHON: PythonParser(),
    Language.JAVASCRIPT: TreeSitterParser(Language.JAVASCRIPT),
    Language.TYPESCRIPT: TreeSitterParser(Language.TYPESCRIPT),
}
```

`SourceParser` is a `Protocol` with the existing `parse_file(*, source,
repo_id, file_path, commit_sha) -> list[IngestionUnit]` signature —
`PythonParser` already satisfies it unchanged. Files whose language has no
registered parser are skipped exactly as today (walked, counted, no error).

### TreeSitterParser

One engine class; language-specific behavior lives in a per-language rules
table (node type names differ slightly between the JS and TSX grammars).

Extraction rules (full parity with `python_parser.py`):

| Python concept | JS/TS equivalent extracted |
|---|---|
| module unit (whole file) | same — one MODULE unit per file, full source |
| `def f()` / `async def` | `function f()`, `async function`, generator functions; **arrow/function expressions assigned to a `const`/`let`/`var`** (`const useAuth = () => {…}`) emit a FUNCTION unit named after the binding |
| `class C(Base)` | `class C extends Base` → CLASS unit, `bases=["Base"]` |
| methods | class-body `method_definition` (incl. static, getters/setters) → METHOD |
| `UPPER_CASE = …` constant | top-level `const UPPER_CASE = …` (UPPER_CASE name rule, same as Python) → CONSTANT |
| `import x` / `from x import y` | `import … from "mod"` (default/named/namespace), `const x = require("mod")`, `export … from "mod"` → imports list |
| `f()` call walk | `call_expression` walk inside each function body → calls list (dotted member chains reconstructed like `_attr_chain`) |
| `ast.Name` references | identifier references inside function bodies → references list |
| docstring | leading JSDoc block comment (`/** … */`) immediately above the declaration |
| signature | reconstructed def-line: name + parameter list + return type annotation when present (TS) |

Determinism rules carried over verbatim: module unit first, children sorted
by `(line_start, name)`, list fields sorted + deduplicated (the
`IngestionUnit` validator already enforces the latter).

Parse errors: tree-sitter is error-tolerant and always returns a tree.
Policy — if the root node `has_error`, still extract what's parseable
(matching tree-sitter's design intent), but emit a `parse_partial` log event
with the error count. Only truly unreadable input (decode failure) raises,
mirroring the Python parser's `SyntaxError` path so the pipeline's
`failed_files` accounting keeps working.

### Qualified names

JS has no dotted module system, so qnames derive from the repo-relative
path exactly the way Python's do:

- `src/components/Button.tsx` → module qname `src.components.Button`
- function `useAuth` inside it → `src.components.Button.useAuth`
- `index.js` collapses like `__init__.py`: `src/utils/index.js` → `src.utils`

This keeps graph semantics and `stable_unit_id` derivation uniform across
languages. Collision note: `src/a.py` and `src/a.ts` in one repo would share
a module qname but NOT a unit_id (file_path is part of the id hash); the
graph builder MERGEs nodes by qname, so such a node would carry units from
both files. Accepted for now — same-stem same-dir cross-language files are
rare and the units remain distinct.

### Import path normalization

JS imports are file-relative strings, not dotted modules. Normalization rule:

- Relative specifiers (`./x`, `../y/z`) resolve against the importing file's
  directory to a repo-relative path, then convert to the dotted qname form
  (extension dropped, `/index` collapsed). `import { f } from "./scorer"` in
  `app/ats/run.js` → `app.ats.scorer`.
- Bare specifiers (`react`, `lodash/merge`) are package imports — recorded
  as-is (slashes → dots). They become EXTERNAL nodes in the graph, exactly
  like unresolvable Python imports do today.
- Named imports append the symbol: `import { score } from "./scorer"` →
  `app.ats.scorer.score`, mirroring Python's `from x import y` handling.

## Dependencies

Pinned additions to `requirements.lock.txt` (exact versions chosen at
implementation time after wheel verification):

- `tree-sitter` (core bindings)
- `tree-sitter-javascript`
- `tree-sitter-typescript`

**B8/B9 lesson applied:** all three are compiled extensions. Before merge,
verify wheels exist for the production image platform (python:3.12-slim,
linux/amd64) and run the production Docker build locally. The `pip wheel`
builder stage must produce wheels for these without a compiler toolchain.

## Testing

1. **Unit/golden tests** (`tests/parsing/`): fixture `.js`, `.cjs`, `.ts`,
   `.tsx` files with known contents; assert exact `IngestionUnit` lists
   (qnames, kinds, line ranges, imports, calls, bases) — mirroring the
   existing Python parser golden tests. Cases must include: arrow-function
   consts, default + named + namespace imports, `require()`, classes with
   extends, JSX components, generics in signatures, a file with a syntax
   error (partial extraction), a `.d.ts` (walker excludes it).
2. **Walker tests**: extension mapping incl. `.d.ts` exclusion.
3. **Pipeline test**: mixed-language fixture repo (Python + TS in one repo)
   ingests both, registry dispatch correct, unknown extensions still skipped.
4. **Graph test**: JS→JS import and call edges appear; bare-specifier
   imports become EXTERNAL nodes.

Per the storage-test-gap lesson: parser tests are pure (no store mocks
needed — input string, output models), so this PR doesn't add to the mocked
storage problem. The golden integration test (Gap 5) remains separate work.

## Validation plan (post-merge)

1. Local: full test suite + production Docker build.
2. VM: pull main, `build --no-cache api` (lockfile changed), `up -d`.
3. Re-ingest JA4M; confirm the 161 `.cjs`/`.js` files now produce units.
4. Spot-check `query_graph` on a known JS qname and on Python qnames
   (regression: Python counts must be unchanged — 1,245 Python-derived
   units should still be present, plus new JS ones).

## Risks

- **Wheel availability** (B8/B9 class) — mitigated by local Docker build
  verification before merge.
- **Grammar node-name drift** between tree-sitter-javascript and
  tree-sitter-typescript (TSX grammar is a superset but some node types
  differ) — mitigated by the per-language rules table and golden tests for
  every extension.
- **Re-ingest unit-count growth**: JA4M re-ingest will roughly double its
  Postgres/Neo4j/Qdrant footprint. No action needed (volumes have room),
  just expected.
