# Multi-language Batch 2: C#, Go, Java, Rust — Implementation Plan

> **For agentic workers:** orchestrated via subagent-driven development; Tasks 2-5 run in PARALLEL (disjoint files, no commits — orchestrator commits).

**Goal:** Ingest C# (validation repo: the user's Unity game at ~/Desktop/void/void, 69 .cs files), Go, Java, Rust with the same unit/edge fidelity as Python/JS/TS.

**Architecture:** `core/parsing/treesitter_parser.py` becomes a thin dispatcher; per-language extraction moves to `core/parsing/languages/<lang>.py` modules implementing a common interface (`extract_children(root, inputs)`, `extract_imports(root, inputs)`), sharing helpers from `core/parsing/languages/_shared.py` (the current `_member_chain`/`_signature`/`_jsdoc_for`/`_slice_source`/constant-name helpers move there; JS/TS rules move to `languages/javascript.py` unchanged in behavior — all 574 existing tests must stay green).

**Deps (wheels verified linux x86_64 + mac arm):** `tree-sitter-c-sharp==0.23.5`, `tree-sitter-go==0.25.0`, `tree-sitter-java==0.23.5`, `tree-sitter-rust==0.24.2`.

**Qname scheme:** path-based dotted qnames for every language (design decision D-16 — uniform graph semantics). Per-language conventions: Go `mod` collapse? no — Go files: path-based, `pkg/server/handler.go` → `pkg.server.handler`; Java: path-based (src dirs stay in the qname; acceptable); Rust: `src/lib.rs` → `src.lib`, `mod.rs` collapses like `index.js`; C#: path-based (namespaces recorded in unit metadata only via parent chain of nested declarations).

**Unit mappings (each language agent MUST probe the real grammar before writing rules — verified node names go in the module docstring):**

| Language | MODULE | CLASS | FUNCTION | METHOD | CONSTANT | bases | imports | calls |
|---|---|---|---|---|---|---|---|---|
| C# | file | class/struct/interface/record/enum decl | local fns (rare) | method/constructor/property-with-body in type bodies | const fields + UPPER static readonly | base_list | using_directive | invocation_expression (+object_creation_expression) |
| Go | file | type_declaration struct/interface | function_declaration | method_declaration (receiver type → parent qname) | const_declaration specs (UPPER rule relaxed: ALL top-level consts — Go convention is CamelCase) | struct embedding (best-effort) | import_declaration (paths as dotted) | call_expression |
| Java | file | class/interface/enum/record decl | (rare) | method_declaration + constructor_declaration | static final UPPER fields | superclass + super_interfaces | import_declaration | method_invocation + object_creation |
| Rust | file | struct_item/enum_item/trait_item | function_item | fns inside impl_item (impl type → parent qname; trait impls `Type` parent) | const_item/static_item | trait in `impl Trait for Type` (best-effort bases on the type) | use_declaration (`::`→`.`) | call_expression (path reconstructed `::`→`.`) |

Nested declarations (C# nested classes, Rust nested mods): one level of nesting minimum (parity with Python's class-in-module); deeper nesting flattened onto parent qname chain is fine.

Doc comments: C# `///` runs, Go `//` run directly above decl, Java `/** */`, Rust `///`/`//!` — each maps to docstring, cleaned.

### Task 1 (sequential, first): skeleton + JS/TS relocation
- `Language` enum += CSHARP/GO/JAVA/RUST; walker rows `.cs/.go/.java/.rs` (+ `.cs` only — no `.csx`; Go: skip `_test.go`? NO — include, tests are code); `_default_parsers()` entries; grammar plumbing for 4 packages; `core/parsing/languages/` package with `_shared.py` + `javascript.py` (relocated logic) + 4 stub modules raising-free (return []); deps in pyproject + lockfile + venv install; walker/enum tests; ALL 574 existing tests green after relocation (treesitter test file untouched and passing proves behavior preserved).

### Tasks 2-5 (PARALLEL, no commits, disjoint files): one per language
Each agent: probe grammar empirically (script every construct in the mapping table) → implement `core/parsing/languages/<lang>.py` → fixtures `tests/fixtures/sample_repo_<lang>/` (2-3 small files exercising classes/functions/imports/calls/inheritance) → tests `tests/test_<lang>_parser.py` (mirror test_treesitter_parser.py idiom: exact qnames, kinds, signatures, imports, calls, bases, docstrings, determinism). C# agent additionally probes against 2-3 REAL files from ~/Desktop/void/void/Assets (read-only) and pins one realistic Unity pattern (MonoBehaviour subclass with [SerializeField] fields + methods).

### Task 6 (sequential): integration + gates + ship
- Extend `tests/integration/test_storage_golden.py` with one polyglot fixture pass (or per-language counts) against real stores; run it live via the test compose stack.
- Full pytest, ruff, mypy-no-new, `npm` untouched; production Docker build + container import smoke for all 4 grammars; commit everything, PR, merge.

### Task 7: deploy + real-world validation
- VM: pull, `build --no-cache api` (lockfile changed!), up -d.
- rsync ~/Desktop/void/void → VM repos/void (excludes += Library/ Logs/ obj/ *.meta — Unity), ingest, expect ~69 .cs parsed; spot-check qnames, query_graph on a game class, semantic retrieve ("player movement" etc.). Update memory.
