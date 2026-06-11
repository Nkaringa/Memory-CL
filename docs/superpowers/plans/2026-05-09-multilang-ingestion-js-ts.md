# JS/TS Ingestion via Tree-sitter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse JavaScript and TypeScript (incl. JSX/TSX) into `IngestionUnit`s with full parity to the Python parser, so the graph/retrieval tools work identically on JS/TS code.

**Architecture:** A new `TreeSitterParser` class emits the existing language-agnostic `IngestionUnit` model; the pipeline dispatches per-file via a `dict[Language, SourceParser]` registry. Storage layers and graph builder are untouched except for one qname-derivation helper that must understand JS paths.

**Tech Stack:** `tree-sitter==0.25.2`, `tree-sitter-javascript==0.25.0`, `tree-sitter-typescript==0.23.2` (all verified to ship cp312/abi3 manylinux x86_64 + aarch64 wheels). Python 3.12, pytest.

**Spec:** `docs/superpowers/specs/2026-05-09-multilang-ingestion-js-ts-design.md`

**Branch:** `feat/multilang-ingestion-js-ts` (already created)

## Verified grammar facts (probed against the real packages — do not re-derive)

- API: `Language(tree_sitter_javascript.language())`, `Parser(lang)`, `parser.parse(bytes)`. TS package exposes `language_typescript()` and `language_tsx()`.
- `node.start_point.row` / `end_point.row` are **0-indexed** rows → line numbers need `+1`.
- Top-level node types (JS grammar): `function_declaration`, `generator_function_declaration`, `class_declaration`, `lexical_declaration`/`variable_declaration` → `variable_declarator` (fields `name`, `value`), `import_statement` (field `source` → `string` node containing `string_fragment`), `export_statement` (field `declaration` when wrapping a decl; field `source` + `export_clause` when re-exporting), `comment`.
- `variable_declarator` value types for function bindings: `arrow_function`, `function_expression`, `generator_function`.
- Arrow functions: field `parameters` (parenthesized) **or** field `parameter` (bare `x => x`). Async is a plain `async` child token: `any(c.type == "async" for c in node.children)`.
- Classes: field `name` is `identifier` (JS) / `type_identifier` (TS). `class_heritage` children: JS grammar → bare `identifier` / `member_expression`; TS grammar → `extends_clause` (field `value`) + `implements_clause`.
- Class body members: `method_definition` (fields `name`, `parameters`, optional `return_type`, `body`), `field_definition` (JS) / `public_field_definition` (TS) with fields `name`, `value`.
- `call_expression` field `function` is `identifier` | `member_expression` (fields `object`, `property`) | other (subscript, parenthesized — skip those).
- `import_specifier` fields: `name`, optional `alias`. `namespace_import` and default-import `identifier` carry no per-symbol names.
- `require`: `variable_declarator` whose `value` is `call_expression` with `function` text `require`.
- TS-only top-level types we deliberately **skip**: `interface_declaration`, `type_alias_declaration`, `enum_declaration`, `ambient_declaration`.
- JSDoc: a `comment` node that is `prev_named_sibling` of the declaration (or of its `export_statement` wrapper) and starts with `/**`.
- Error tolerance: malformed input still yields a tree; `root.has_error` is True; parseable siblings still appear.

---

### Task 1: Shared qname helper (`core/parsing/qnames.py`)

The qname-from-path rule is about to be needed in three places (python_parser, treesitter_parser, graph_builder). Extract it once.

**Files:**
- Create: `core/parsing/qnames.py`
- Modify: `core/parsing/python_parser.py` (delete local def, import instead)
- Modify: `core/parsing/__init__.py`
- Test: `tests/test_qnames.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_qnames.py`:

```python
from __future__ import annotations

from core.parsing.qnames import module_qname_from_path


def test_python_paths() -> None:
    assert module_qname_from_path("pkg/mod.py") == "pkg.mod"
    assert module_qname_from_path("pkg/__init__.py") == "pkg"
    assert module_qname_from_path("top.py") == "top"
    # `index.py` must NOT collapse — index-collapse is a JS-world rule.
    assert module_qname_from_path("pkg/index.py") == "pkg.index"


def test_js_ts_paths() -> None:
    assert module_qname_from_path("src/app.js") == "src.app"
    assert module_qname_from_path("src/components/Button.tsx") == "src.components.Button"
    assert module_qname_from_path("src/utils/index.js") == "src.utils"
    assert module_qname_from_path("src/utils/index.ts") == "src.utils"
    assert module_qname_from_path("lib/loader.cjs") == "lib.loader"
    assert module_qname_from_path("lib/esm.mjs") == "lib.esm"
    # `__init__.js` is not a Python file — no collapse.
    assert module_qname_from_path("src/__init__.js") == "src.__init__"


def test_suffixless_paths_collapse_index() -> None:
    # Import-resolution calls this with already-stripped paths.
    assert module_qname_from_path("src/utils/index") == "src.utils"
    assert module_qname_from_path("src/utils") == "src.utils"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_qnames.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.parsing.qnames'`

- [ ] **Step 3: Write the implementation**

Create `core/parsing/qnames.py`:

```python
from __future__ import annotations

# Source-file suffixes the ingestion layer understands, used both for
# qname derivation and import-specifier normalization. Order matters
# only in that every entry must be matched by exact `endswith`.
SOURCE_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".jsx", ".mjs", ".cjs", ".js",
    ".tsx", ".mts", ".cts", ".ts",
)


def module_qname_from_path(file_path: str) -> str:
    """Convert a repo-relative POSIX path to a dotted module qname.

    Examples:
        "pkg/mod.py"          -> "pkg.mod"
        "pkg/__init__.py"     -> "pkg"
        "src/app.js"          -> "src.app"
        "src/utils/index.ts"  -> "src.utils"
        "src/utils/index"     -> "src.utils"   (suffix already stripped)

    `__init__` collapse applies only to Python files; `index` collapse
    applies only to JS/TS files (and suffixless paths, which only the
    JS import resolver produces).
    """
    is_python = False
    stem_path = file_path
    for suffix in SOURCE_SUFFIXES:
        if file_path.endswith(suffix):
            stem_path = file_path[: -len(suffix)]
            is_python = suffix == ".py"
            break
    parts = stem_path.split("/")
    if parts and (
        (is_python and parts[-1] == "__init__")
        or (not is_python and parts[-1] == "index")
    ):
        parts = parts[:-1]
    return ".".join(parts)
```

- [ ] **Step 4: Point `python_parser.py` at the shared helper**

In `core/parsing/python_parser.py`, delete the local `module_qname_from_path` function (lines 23-36, the whole def including its docstring) and add to the imports block at the top:

```python
from core.parsing.qnames import module_qname_from_path
```

In `core/parsing/__init__.py`, replace the whole file with:

```python
from core.parsing.file_walker import FileWalker, WalkResult
from core.parsing.python_parser import PythonParser
from core.parsing.qnames import module_qname_from_path

__all__ = ["FileWalker", "PythonParser", "WalkResult", "module_qname_from_path"]
```

(`module_qname_from_path` stays importable from both `core.parsing` and `core.parsing.python_parser` — existing tests import from both.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_qnames.py tests/test_python_parser.py -v`
Expected: ALL PASS (the python-parser golden tests prove no behavior change)

- [ ] **Step 6: Commit**

```bash
git add core/parsing/qnames.py core/parsing/python_parser.py core/parsing/__init__.py tests/test_qnames.py
git commit -m "refactor(parsing): extract shared module_qname_from_path with JS/TS support"
```

---

### Task 2: Make `graph_builder._module_qname` multi-language

`graph_builder.py` keeps a deliberately-inlined mirror of the qname rule (its comment explains why: narrow import surface). Update the mirror and pin it in sync with a test.

**Files:**
- Modify: `core/ingestion/graph_builder.py:45-57`
- Test: `tests/test_qnames.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_qnames.py`:

```python
def test_graph_builder_mirror_stays_in_sync() -> None:
    """graph_builder inlines a mirror of module_qname_from_path on purpose
    (narrow import surface). This test fails if the two ever drift."""
    from core.ingestion.graph_builder import _module_qname

    for path in (
        "pkg/mod.py", "pkg/__init__.py", "pkg/index.py",
        "src/app.js", "src/utils/index.ts", "src/components/Button.tsx",
        "lib/loader.cjs", "top.py",
    ):
        assert _module_qname(path) == module_qname_from_path(path), path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_qnames.py::test_graph_builder_mirror_stays_in_sync -v`
Expected: FAIL on `"src/utils/index.ts"` (old mirror returns `"src.utils.index.ts"`)

- [ ] **Step 3: Update the mirror**

In `core/ingestion/graph_builder.py`, replace the `_module_qname` function (lines 45-57) with:

```python
_SOURCE_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".jsx", ".mjs", ".cjs", ".js",
    ".tsx", ".mts", ".cts", ".ts",
)


def _module_qname(file_path: str) -> str:
    """Mirror of `core.parsing.qnames.module_qname_from_path`.

    Inlined to keep this module's import surface narrow — graph_builder
    already depends on parsing semantically; pulling the function in
    would create a redundant runtime dependency edge. Kept in sync by
    tests/test_qnames.py::test_graph_builder_mirror_stays_in_sync.
    """
    is_python = False
    stem_path = file_path
    for suffix in _SOURCE_SUFFIXES:
        if file_path.endswith(suffix):
            stem_path = file_path[: -len(suffix)]
            is_python = suffix == ".py"
            break
    parts = stem_path.split("/")
    if parts and (
        (is_python and parts[-1] == "__init__")
        or (not is_python and parts[-1] == "index")
    ):
        parts = parts[:-1]
    return ".".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_qnames.py tests/test_graph_builder.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add core/ingestion/graph_builder.py tests/test_qnames.py
git commit -m "fix(graph): teach _module_qname the JS/TS path rules (with sync-pin test)"
```

---

### Task 3: `Language` enum + walker extensions + `.d.ts` exclusion

**Files:**
- Modify: `schemas/ingest.py:26-28`
- Modify: `core/parsing/file_walker.py:47-90`
- Test: `tests/test_file_walker.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_file_walker.py` (match its existing helpers/imports — it already imports `FileWalker` and `Language`; add fixture files via `tmp_path` like its other tests do):

```python
def test_walker_maps_js_ts_extensions(tmp_path) -> None:
    (tmp_path / "a.js").write_text("const x = 1;")
    (tmp_path / "b.mjs").write_text("export const y = 1;")
    (tmp_path / "c.cjs").write_text("module.exports = {};")
    (tmp_path / "d.jsx").write_text("export default () => null;")
    (tmp_path / "e.ts").write_text("const z: number = 1;")
    (tmp_path / "f.tsx").write_text("export default () => null;")
    (tmp_path / "g.mts").write_text("export {};")
    (tmp_path / "h.cts").write_text("export {};")
    (tmp_path / "skip.css").write_text("body {}")

    result = FileWalker().walk(tmp_path, repo_id="r")
    langs = {f.path: f.language for f in result.files}

    assert langs == {
        "a.js": Language.JAVASCRIPT,
        "b.mjs": Language.JAVASCRIPT,
        "c.cjs": Language.JAVASCRIPT,
        "d.jsx": Language.JAVASCRIPT,
        "e.ts": Language.TYPESCRIPT,
        "f.tsx": Language.TYPESCRIPT,
        "g.mts": Language.TYPESCRIPT,
        "h.cts": Language.TYPESCRIPT,
    }


def test_walker_skips_declaration_files(tmp_path) -> None:
    (tmp_path / "real.ts").write_text("const x = 1;")
    (tmp_path / "types.d.ts").write_text("declare const x: number;")
    (tmp_path / "esm.d.mts").write_text("declare const y: number;")
    (tmp_path / "cjs.d.cts").write_text("declare const z: number;")

    result = FileWalker().walk(tmp_path, repo_id="r")
    assert [f.path for f in result.files] == ["real.ts"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_file_walker.py -v -k "js_ts or declaration"`
Expected: FAIL — `AttributeError: JAVASCRIPT` (enum value doesn't exist yet)

- [ ] **Step 3: Implement**

In `schemas/ingest.py`, replace the `Language` enum (lines 26-28) with:

```python
class Language(StrEnum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    # Reserved for later phases — adding values here is backward-compatible.
```

In `core/parsing/file_walker.py`:

1. Replace the `LANGUAGE_EXTENSIONS` table and the class docstring's second paragraph:

```python
    LANGUAGE_EXTENSIONS: tuple[tuple[str, Language], ...] = (
        (".py", Language.PYTHON),
        (".js", Language.JAVASCRIPT),
        (".mjs", Language.JAVASCRIPT),
        (".cjs", Language.JAVASCRIPT),
        (".jsx", Language.JAVASCRIPT),
        (".ts", Language.TYPESCRIPT),
        (".tsx", Language.TYPESCRIPT),
        (".mts", Language.TYPESCRIPT),
        (".cts", Language.TYPESCRIPT),
    )

    # TypeScript declaration files carry types only, no logic. Their
    # suffix per `path.suffix` is just ".ts"/".mts"/".cts", so they need
    # a name-based check, not a suffix-table entry.
    _DECLARATION_SUFFIXES: tuple[str, ...] = (".d.ts", ".d.mts", ".d.cts")
```

(Update the class docstring line "Phase 2 walks for `*.py` only..." to: "Walks Python and JS/TS sources. Adding more languages later is a pure additive change to `LANGUAGE_EXTENSIONS`.")

2. In `walk()`, add the declaration-file check right after the `spec.match_file` check (before the `ext_to_lang.get` line):

```python
            if path.name.endswith(self._DECLARATION_SUFFIXES):
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_file_walker.py tests/test_schemas.py tests/test_ingest_schema.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add schemas/ingest.py core/parsing/file_walker.py tests/test_file_walker.py
git commit -m "feat(walker): map JS/TS extensions to new Language values, skip .d.ts"
```

---

### Task 4: Dependencies

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Modify: `requirements.lock.txt`

- [ ] **Step 1: Add to `pyproject.toml`**

In the `dependencies = [` list, add (keep alphabetical position with the existing entries):

```toml
    "tree-sitter==0.25.2",
    "tree-sitter-javascript==0.25.0",
    "tree-sitter-typescript==0.23.2",
```

- [ ] **Step 2: Add to `requirements.lock.txt`**

Add a new section (before any trailing comments, following the file's section style):

```
# --- Parsing (multi-language ingestion) -------------------------------------
tree-sitter==0.25.2
tree-sitter-javascript==0.25.0
tree-sitter-typescript==0.23.2
```

- [ ] **Step 3: Install + smoke-test imports**

The packages are already in the venv from plan research; re-run to be sure:

```bash
.venv/bin/pip install tree-sitter==0.25.2 tree-sitter-javascript==0.25.0 tree-sitter-typescript==0.23.2
.venv/bin/python -c "
import tree_sitter_javascript, tree_sitter_typescript
from tree_sitter import Language, Parser
Parser(Language(tree_sitter_javascript.language()))
Parser(Language(tree_sitter_typescript.language_typescript()))
Parser(Language(tree_sitter_typescript.language_tsx()))
print('ok')
"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml requirements.lock.txt
git commit -m "build: add tree-sitter + JS/TS grammar wheels to deps"
```

---

### Task 5: `TreeSitterParser` skeleton — module unit + error tolerance

**Files:**
- Create: `core/parsing/treesitter_parser.py`
- Modify: `core/parsing/__init__.py`
- Test: `tests/test_treesitter_parser.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_treesitter_parser.py`:

```python
from __future__ import annotations

import textwrap

from core.parsing import TreeSitterParser
from schemas import Language, UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"


def _parse(source: str, file_path: str = "src/app.js") -> list:
    lang = (
        Language.TYPESCRIPT
        if file_path.endswith((".ts", ".tsx", ".mts", ".cts"))
        else Language.JAVASCRIPT
    )
    return TreeSitterParser(lang).parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


def test_module_unit_first_with_full_source() -> None:
    units = _parse("const x = 1;\n")
    assert units[0].kind == UnitKind.MODULE
    assert units[0].qualified_name == "src.app"
    assert units[0].name == "app"
    assert units[0].language == Language.JAVASCRIPT
    assert units[0].content == "const x = 1;\n"
    assert units[0].line_start == 1


def test_index_file_collapses_module_qname() -> None:
    units = _parse("const x = 1;\n", file_path="src/utils/index.js")
    assert units[0].qualified_name == "src.utils"
    assert units[0].name == "utils"


def test_typescript_module_language() -> None:
    units = _parse("const x: number = 1;\n", file_path="src/lib.ts")
    assert units[0].language == Language.TYPESCRIPT


def test_syntax_error_still_returns_module_unit() -> None:
    # Broken function followed by a healthy const — error-tolerant parse.
    units = _parse("function broken( { if (x {\nconst X = 1;\n")
    assert units[0].kind == UnitKind.MODULE


def test_module_docstring_from_leading_block_comment() -> None:
    units = _parse("""
        /* App entry point. */
        const x = 1;
    """)
    assert units[0].docstring == "App entry point."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_treesitter_parser.py -v`
Expected: FAIL with `ImportError: cannot import name 'TreeSitterParser'`

- [ ] **Step 3: Write the skeleton implementation**

Create `core/parsing/treesitter_parser.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache

import tree_sitter_javascript as _tsjs
import tree_sitter_typescript as _tsts
from tree_sitter import Language as _TSLanguage
from tree_sitter import Node
from tree_sitter import Parser as _TSParser

from core.ingestion.logevent import emit_phase2_event
from core.observability import get_tracer
from core.parsing.qnames import module_qname_from_path
from schemas import (
    IngestionUnit,
    Language,
    UnitKind,
    content_sha,
    stable_unit_id,
)

_tracer = get_tracer("core.parsing.treesitter_parser")


# ---------------------------------------------------------------------------
# Grammar plumbing
# ---------------------------------------------------------------------------
@lru_cache(maxsize=3)
def _grammar_parser(grammar: str) -> _TSParser:
    """One Parser per grammar, shared process-wide (parsers are reusable)."""
    if grammar == "javascript":
        lang = _TSLanguage(_tsjs.language())
    elif grammar == "tsx":
        lang = _TSLanguage(_tsts.language_tsx())
    else:
        lang = _TSLanguage(_tsts.language_typescript())
    return _TSParser(lang)


def _grammar_for(language: Language, file_path: str) -> str:
    if language is Language.JAVASCRIPT:
        return "javascript"  # the JS grammar includes JSX
    return "tsx" if file_path.endswith(".tsx") else "typescript"


# ---------------------------------------------------------------------------
# Small node helpers
# ---------------------------------------------------------------------------
def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8")


def _slice_source(source: str, line_start: int, line_end: int) -> str:
    """Return raw source for [line_start, line_end] inclusive (1-indexed)."""
    lines = source.splitlines(keepends=True)
    return "".join(lines[line_start - 1 : line_end])


def _clean_block_comment(text: str) -> str | None:
    """Strip /** ... */ (or /* ... */) decoration down to the prose."""
    body = text
    if body.startswith("/**"):
        body = body[3:]
    elif body.startswith("/*"):
        body = body[2:]
    if body.endswith("*/"):
        body = body[:-2]
    lines = [ln.strip().lstrip("*").strip() for ln in body.splitlines()]
    cleaned = "\n".join(ln for ln in lines if ln)
    return cleaned or None


# Top-level node types that can carry a leading JSDoc. Used to decide
# whether a leading comment documents the module or the first decl.
_DOCUMENTABLE_TYPES = frozenset({
    "function_declaration",
    "generator_function_declaration",
    "class_declaration",
    "lexical_declaration",
    "variable_declaration",
    "export_statement",
})


def _module_docstring(root: Node) -> str | None:
    children = root.named_children
    first = children[0] if children else None
    if first is None or first.type != "comment":
        return None
    text = _text(first)
    if not text.startswith("/*"):
        return None
    nxt = first.next_named_sibling
    if text.startswith("/**") and nxt is not None and nxt.type in _DOCUMENTABLE_TYPES:
        # A JSDoc immediately above a declaration belongs to the decl.
        return None
    return _clean_block_comment(text)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class _ParseInputs:
    source: str
    repo_id: str
    file_path: str
    commit_sha: str
    module_qname: str
    language: Language


class TreeSitterParser:
    """Convert JS/TS source into a deterministic list of `IngestionUnit`s.

    Same contract as `PythonParser.parse_file`: module unit first,
    children sorted by `(line_start, name)`. Tree-sitter is
    error-tolerant — files with syntax errors still yield whatever
    units parsed cleanly, with a `parse_partial` event emitted.
    """

    def __init__(self, language: Language) -> None:
        if language not in (Language.JAVASCRIPT, Language.TYPESCRIPT):
            raise ValueError(f"TreeSitterParser does not handle {language}")
        self._language = language

    def parse_file(
        self,
        *,
        source: str,
        repo_id: str,
        file_path: str,
        commit_sha: str,
    ) -> list[IngestionUnit]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("treesitter_parser.parse_file") as span:
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("file_path", file_path)

            grammar = _grammar_for(self._language, file_path)
            tree = _grammar_parser(grammar).parse(source.encode("utf-8"))
            root = tree.root_node

            if root.has_error:
                emit_phase2_event(
                    event="parse_partial",
                    operation="treesitter_parser.parse_file",
                    status="partial",
                    duration_ms=(time.perf_counter() - start) * 1000,
                    file_path=file_path,
                    level="warning",
                )

            module_qname = module_qname_from_path(file_path)
            inputs = _ParseInputs(
                source=source,
                repo_id=repo_id,
                file_path=file_path,
                commit_sha=commit_sha,
                module_qname=module_qname,
                language=self._language,
            )

            children = _extract_children(root, inputs)
            module_unit = _make_unit(
                inputs=inputs,
                kind=UnitKind.MODULE,
                name=module_qname.split(".")[-1] if module_qname else file_path,
                qualified_name=module_qname,
                parent_qualified_name=None,
                line_start=1,
                line_end=max(1, source.count("\n") + 1),
                content=source,
                docstring=_module_docstring(root),
                signature=None,
                imports=_extract_imports(root, inputs),
                calls=[],
                references=[],
                bases=[],
            )

            children.sort(key=lambda u: (u.line_start, u.name))
            units = [module_unit, *children]

            duration = (time.perf_counter() - start) * 1000
            emit_phase2_event(
                event="parse_ok",
                operation="treesitter_parser.parse_file",
                status="success",
                duration_ms=duration,
                file_path=file_path,
                content_hash=content_sha(source),
                level="debug",
                units_emitted=len(units),
            )
            span.set_attribute("units_emitted", len(units))
            return units


# ---------------------------------------------------------------------------
# Extractors — filled in by subsequent tasks
# ---------------------------------------------------------------------------
def _extract_children(root: Node, inputs: _ParseInputs) -> list[IngestionUnit]:
    return []


def _extract_imports(root: Node, inputs: _ParseInputs) -> list[str]:
    return []


def _make_unit(
    *,
    inputs: _ParseInputs,
    kind: UnitKind,
    name: str,
    qualified_name: str,
    parent_qualified_name: str | None,
    line_start: int,
    line_end: int,
    content: str,
    docstring: str | None,
    signature: str | None,
    imports: list[str],
    calls: list[str],
    references: list[str],
    bases: list[str],
) -> IngestionUnit:
    return IngestionUnit(
        unit_id=stable_unit_id(inputs.repo_id, inputs.file_path, qualified_name),
        repo_id=inputs.repo_id,
        commit_sha=inputs.commit_sha,
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        parent_qualified_name=parent_qualified_name,
        file_path=inputs.file_path,
        language=inputs.language,
        line_start=line_start,
        line_end=line_end,
        content=content,
        source_sha=content_sha(content),
        docstring=docstring,
        signature=signature,
        imports=imports,
        calls=calls,
        references=references,
        bases=bases,
    )
```

Update `core/parsing/__init__.py` to:

```python
from core.parsing.file_walker import FileWalker, WalkResult
from core.parsing.python_parser import PythonParser
from core.parsing.qnames import module_qname_from_path
from core.parsing.treesitter_parser import TreeSitterParser

__all__ = [
    "FileWalker",
    "PythonParser",
    "TreeSitterParser",
    "WalkResult",
    "module_qname_from_path",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_treesitter_parser.py -v`
Expected: ALL 5 PASS

- [ ] **Step 5: Commit**

```bash
git add core/parsing/treesitter_parser.py core/parsing/__init__.py tests/test_treesitter_parser.py
git commit -m "feat(parsing): TreeSitterParser skeleton — module units, error tolerance"
```

---

### Task 6: Declarations — functions, classes, methods, constants

**Files:**
- Modify: `core/parsing/treesitter_parser.py` (replace `_extract_children` stub, add helpers)
- Test: `tests/test_treesitter_parser.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_treesitter_parser.py`:

```python
def test_function_declarations() -> None:
    units = _parse("""
        function plain(a, b) { return a; }
        async function fetcher(url) { return url; }
        function* gen(x) { yield x; }
        export default function App() { return null; }
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.plain"].kind == UnitKind.FUNCTION
    assert by_qname["src.app.plain"].signature == "plain(a, b)"
    assert by_qname["src.app.fetcher"].signature == "async fetcher(url)"
    assert by_qname["src.app.gen"].kind == UnitKind.FUNCTION
    assert by_qname["src.app.App"].kind == UnitKind.FUNCTION


def test_arrow_and_function_expression_bindings() -> None:
    units = _parse("""
        const useAuth = (token) => token;
        const bare = x => x;
        const legacy = function(a) { return a; };
        export const exported = async () => 1;
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.useAuth"].kind == UnitKind.FUNCTION
    assert by_qname["src.app.useAuth"].signature == "useAuth(token)"
    assert by_qname["src.app.bare"].signature == "bare(x)"
    assert by_qname["src.app.legacy"].kind == UnitKind.FUNCTION
    assert by_qname["src.app.exported"].signature == "async exported()"


def test_class_with_methods_fields_and_extends() -> None:
    units = _parse("""
        class Service extends BaseService {
          static VERSION = "1";
          constructor(cfg) { this.cfg = cfg; }
          async handle(req) { return req; }
          onClick = () => 1;
        }
        class Plain extends React.Component {}
    """)
    by_qname = {u.qualified_name: u for u in units}
    svc = by_qname["src.app.Service"]
    assert svc.kind == UnitKind.CLASS
    assert svc.bases == ["BaseService"]
    assert by_qname["src.app.Service.constructor"].kind == UnitKind.METHOD
    assert by_qname["src.app.Service.handle"].kind == UnitKind.METHOD
    assert by_qname["src.app.Service.handle"].signature == "async handle(req)"
    # Class-property arrow functions are methods in practice (React).
    assert by_qname["src.app.Service.onClick"].kind == UnitKind.METHOD
    # UPPER_CASE static field is a constant.
    assert by_qname["src.app.Service.VERSION"].kind == UnitKind.CONSTANT
    # parent chain
    assert by_qname["src.app.Service.handle"].parent_qualified_name == "src.app.Service"
    # member-expression base
    assert by_qname["src.app.Plain"].bases == ["React.Component"]


def test_top_level_constants_upper_case_only() -> None:
    units = _parse("""
        const MAX_RETRIES = 5;
        const lower = 1;
        export const NAMED_EXPORT = 2;
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.MAX_RETRIES"].kind == UnitKind.CONSTANT
    assert by_qname["src.app.NAMED_EXPORT"].kind == UnitKind.CONSTANT
    assert "src.app.lower" not in by_qname


def test_jsdoc_becomes_docstring() -> None:
    units = _parse("""
        /**
         * Fetches the user.
         * @param token auth token
         */
        const useAuth = (token) => token;
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.app.useAuth"].docstring == "Fetches the user.\n@param token auth token"


def test_typescript_signatures_and_skipped_type_decls() -> None:
    units = _parse(
        """
        interface Props { name: string }
        type Alias = string;
        enum Color { Red }
        export function score(a: number, b: number): number { return a + b; }
        class Svc extends Base implements IFace {
          handle(req: Request): void {}
        }
        """,
        file_path="src/lib.ts",
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.lib.score"].signature == "score(a: number, b: number): number"
    assert by_qname["src.lib.Svc"].bases == ["Base"]  # implements is not inheritance
    assert by_qname["src.lib.Svc.handle"].kind == UnitKind.METHOD
    # Type-level declarations carry no runtime logic — skipped.
    assert "src.lib.Props" not in by_qname
    assert "src.lib.Alias" not in by_qname
    assert "src.lib.Color" not in by_qname


def test_tsx_component_extracted() -> None:
    units = _parse(
        "export default function App() { return <div>hi</div>; }\n",
        file_path="src/App.tsx",
    )
    by_qname = {u.qualified_name: u for u in units}
    assert by_qname["src.App.App"].kind == UnitKind.FUNCTION


def test_children_sorted_by_line() -> None:
    units = _parse("""
        function b() {}
        function a() {}
        const C = 1;
    """)
    rest = units[1:]
    starts = [u.line_start for u in rest]
    assert starts == sorted(starts)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_treesitter_parser.py -v`
Expected: the 8 new tests FAIL (qnames missing from empty extraction); the 5 from Task 5 still PASS

- [ ] **Step 3: Implement extraction**

In `core/parsing/treesitter_parser.py`, add these module-level constants after `_DOCUMENTABLE_TYPES`:

```python
_FUNCTION_DECL_TYPES = frozenset({
    "function_declaration",
    "generator_function_declaration",
})
_FUNCTION_VALUE_TYPES = frozenset({
    "arrow_function",
    "function_expression",
    "generator_function",
})
_DECL_CONTAINER_TYPES = frozenset({
    "lexical_declaration",
    "variable_declaration",
})
_FIELD_DEF_TYPES = frozenset({
    "field_definition",          # JS grammar
    "public_field_definition",   # TS grammar
})
```

Replace the `_extract_children` stub with:

```python
def _jsdoc_for(node: Node) -> str | None:
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        text = _text(prev)
        if text.startswith("/**"):
            return _clean_block_comment(text)
    return None


def _is_constant_name(name: str) -> bool:
    """Same UPPER_CASE rule as the Python parser."""
    return name.isupper() and name.replace("_", "").isalnum()


def _is_async(node: Node) -> bool:
    return any(c.type == "async" for c in node.children)


def _signature(name: str, node: Node) -> str:
    params = node.child_by_field_name("parameters")
    if params is not None:
        params_text = _text(params)
    else:
        # Bare-identifier arrow param: `x => x`.
        bare = node.child_by_field_name("parameter")
        params_text = f"({_text(bare)})" if bare is not None else "()"
    ret = node.child_by_field_name("return_type")
    ret_text = _text(ret) if ret is not None else ""
    prefix = "async " if _is_async(node) else ""
    return f"{prefix}{name}{params_text}{ret_text}"


def _member_chain(node: Node | None) -> str | None:
    """Reconstruct a dotted name from member_expression/identifier chains.

    Mirrors python_parser._attr_chain: unresolvable shapes (subscripts,
    parenthesized expressions, call results) return None — skipped
    rather than emitted as noise.
    """
    if node is None:
        return None
    parts: list[str] = []
    cur: Node | None = node
    while cur is not None and cur.type == "member_expression":
        prop = cur.child_by_field_name("property")
        if prop is None:
            return None
        parts.append(_text(prop))
        cur = cur.child_by_field_name("object")
    if cur is not None and cur.type in ("identifier", "this"):
        parts.append(_text(cur))
        return ".".join(reversed(parts))
    return None


def _extract_children(root: Node, inputs: _ParseInputs) -> list[IngestionUnit]:
    out: list[IngestionUnit] = []
    parent = inputs.module_qname
    for node in root.named_children:
        decl = node
        if node.type == "export_statement":
            inner = node.child_by_field_name("declaration")
            if inner is None:
                continue  # export clause / re-export — no declaration
            decl = inner
        doc = _jsdoc_for(node)  # JSDoc sits above the export wrapper
        if decl.type in _FUNCTION_DECL_TYPES:
            out.append(
                _emit_function(decl, inputs, parent, kind=UnitKind.FUNCTION, docstring=doc)
            )
        elif decl.type == "class_declaration":
            out.extend(_emit_class(decl, inputs, parent, docstring=doc))
        elif decl.type in _DECL_CONTAINER_TYPES:
            out.extend(_emit_declarators(decl, inputs, parent, docstring=doc))
    return out


def _emit_declarators(
    container: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    docstring: str | None,
) -> list[IngestionUnit]:
    out: list[IngestionUnit] = []
    for declarator in container.named_children:
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        if name_node is None or name_node.type != "identifier":
            continue  # destructuring patterns — no single unit name
        name = _text(name_node)
        value = declarator.child_by_field_name("value")
        if value is not None and value.type in _FUNCTION_VALUE_TYPES:
            out.append(
                _emit_function(
                    value,
                    inputs,
                    parent_qname,
                    kind=UnitKind.FUNCTION,
                    docstring=docstring,
                    name_override=name,
                    span_node=container,
                )
            )
        elif _is_constant_name(name):
            line_start = container.start_point.row + 1
            line_end = container.end_point.row + 1
            qname = f"{parent_qname}.{name}" if parent_qname else name
            out.append(
                _make_unit(
                    inputs=inputs,
                    kind=UnitKind.CONSTANT,
                    name=name,
                    qualified_name=qname,
                    parent_qualified_name=parent_qname or None,
                    line_start=line_start,
                    line_end=line_end,
                    content=_slice_source(inputs.source, line_start, line_end),
                    docstring=None,
                    signature=None,
                    imports=[],
                    calls=[],
                    references=[],
                    bases=[],
                )
            )
    return out


def _emit_class(
    cls: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    docstring: str | None,
) -> list[IngestionUnit]:
    name_node = cls.child_by_field_name("name")
    if name_node is None:
        return []
    name = _text(name_node)
    qname = f"{parent_qname}.{name}" if parent_qname else name

    # extends targets. JS grammar: class_heritage holds the expression
    # directly; TS grammar wraps it in extends_clause (+ implements_clause,
    # which is type-level and deliberately ignored).
    bases: list[str] = []
    for child in cls.named_children:
        if child.type != "class_heritage":
            continue
        for h in child.named_children:
            if h.type == "extends_clause":
                chain = _member_chain(h.child_by_field_name("value"))
            elif h.type in ("identifier", "member_expression"):
                chain = _member_chain(h)
            else:
                chain = None
            if chain:
                bases.append(chain)

    children: list[IngestionUnit] = []
    body = cls.child_by_field_name("body")
    if body is not None:
        for member in body.named_children:
            doc = _jsdoc_for(member)
            if member.type == "method_definition":
                children.append(
                    _emit_function(member, inputs, qname, kind=UnitKind.METHOD, docstring=doc)
                )
            elif member.type in _FIELD_DEF_TYPES:
                fname_node = member.child_by_field_name("name")
                value = member.child_by_field_name("value")
                if fname_node is None:
                    continue
                fname = _text(fname_node)
                if value is not None and value.type in _FUNCTION_VALUE_TYPES:
                    children.append(
                        _emit_function(
                            value,
                            inputs,
                            qname,
                            kind=UnitKind.METHOD,
                            docstring=doc,
                            name_override=fname,
                            span_node=member,
                        )
                    )
                elif _is_constant_name(fname):
                    line_start = member.start_point.row + 1
                    line_end = member.end_point.row + 1
                    children.append(
                        _make_unit(
                            inputs=inputs,
                            kind=UnitKind.CONSTANT,
                            name=fname,
                            qualified_name=f"{qname}.{fname}",
                            parent_qualified_name=qname,
                            line_start=line_start,
                            line_end=line_end,
                            content=_slice_source(inputs.source, line_start, line_end),
                            docstring=None,
                            signature=None,
                            imports=[],
                            calls=[],
                            references=[],
                            bases=[],
                        )
                    )

    children.sort(key=lambda u: (u.line_start, u.name))

    line_start = cls.start_point.row + 1
    line_end = cls.end_point.row + 1
    cls_unit = _make_unit(
        inputs=inputs,
        kind=UnitKind.CLASS,
        name=name,
        qualified_name=qname,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=docstring,
        signature=None,
        imports=[],
        calls=[],
        references=[],
        bases=bases,
    )
    return [cls_unit, *children]


def _emit_function(
    fn: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    kind: UnitKind,
    docstring: str | None,
    name_override: str | None = None,
    span_node: Node | None = None,
) -> IngestionUnit:
    if name_override is not None:
        name = name_override
    else:
        name_node = fn.child_by_field_name("name")
        name = _text(name_node) if name_node is not None else "<anonymous>"
    qname = f"{parent_qname}.{name}" if parent_qname else name

    span = span_node if span_node is not None else fn
    line_start = span.start_point.row + 1
    line_end = span.end_point.row + 1

    calls, references = _walk_body(fn)

    return _make_unit(
        inputs=inputs,
        kind=kind,
        name=name,
        qualified_name=qname,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=docstring,
        signature=_signature(name, fn),
        imports=[],
        calls=calls,
        references=references,
        bases=[],
    )


def _walk_body(fn: Node) -> tuple[list[str], list[str]]:
    """Collect calls + identifier references — filled in by Task 7."""
    return [], []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_treesitter_parser.py -v`
Expected: ALL 13 PASS

- [ ] **Step 5: Commit**

```bash
git add core/parsing/treesitter_parser.py tests/test_treesitter_parser.py
git commit -m "feat(parsing): JS/TS declaration extraction — functions, classes, methods, constants"
```

---

### Task 7: Calls and references

**Files:**
- Modify: `core/parsing/treesitter_parser.py` (replace `_walk_body` stub)
- Test: `tests/test_treesitter_parser.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_treesitter_parser.py`:

```python
def test_calls_and_references_extracted() -> None:
    units = _parse("""
        function run(input) {
          const user = fetchUser(input);
          api.client.refresh(user);
          this.helper();
          obj["dynamic"]();
          return user;
        }
    """)
    by_qname = {u.qualified_name: u for u in units}
    run = by_qname["src.app.run"]
    # Dotted chains reconstructed; unresolvable subscript call skipped.
    assert run.calls == sorted({"fetchUser", "api.client.refresh", "this.helper"})
    # Identifier references include params and locals (validator dedupes+sorts).
    assert "input" in run.references
    assert "user" in run.references


def test_calls_inside_nested_closures_attributed_to_outer_fn() -> None:
    units = _parse("""
        const handler = () => {
          items.forEach(item => process(item));
        };
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert "process" in by_qname["src.app.handler"].calls
    assert "items.forEach" in by_qname["src.app.handler"].calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_treesitter_parser.py -v -k "calls"`
Expected: 2 FAIL (calls/references empty)

- [ ] **Step 3: Implement `_walk_body`**

Replace the `_walk_body` stub in `core/parsing/treesitter_parser.py` with:

```python
def _walk_body(fn: Node) -> tuple[list[str], list[str]]:
    """Collect call targets + identifier references in a function subtree.

    Mirrors python_parser._emit_function's ast.walk: nested closures are
    NOT separate units, so their calls attribute to the enclosing
    function. Method bodies are walked when their method is emitted.
    """
    calls: list[str] = []
    references: list[str] = []
    body = fn.child_by_field_name("body")
    if body is None:
        return calls, references
    stack: list[Node] = [body]
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            target = _member_chain(node.child_by_field_name("function"))
            if target:
                calls.append(target)
        elif node.type == "identifier":
            references.append(_text(node))
        stack.extend(node.named_children)
    return calls, references
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_treesitter_parser.py -v`
Expected: ALL 15 PASS

- [ ] **Step 5: Commit**

```bash
git add core/parsing/treesitter_parser.py tests/test_treesitter_parser.py
git commit -m "feat(parsing): JS/TS call + reference extraction"
```

---

### Task 8: Imports — extraction + normalization

**Files:**
- Modify: `core/parsing/treesitter_parser.py` (replace `_extract_imports` stub)
- Test: `tests/test_treesitter_parser.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_treesitter_parser.py`:

```python
def test_import_statement_variants() -> None:
    units = _parse(
        """
        import React from "react";
        import { useState, useEffect as ue } from "react";
        import * as path from "node:path";
        import "./styles.css";
        import { score } from "./scorer";
        import { deep } from "../shared/util";
        const fs = require("fs");
        const local = require("./local");
        export { helper } from "./util";
        """,
        file_path="app/ats/run.js",
    )
    module = units[0]
    assert module.imports == sorted({
        "react",                    # default import — module only
        "react.useState",
        "react.useEffect",          # original name, not the alias
        "node:path",                # namespace import — module only
        "app.ats.styles.css",       # side-effect relative import, resolved
        "app.ats.scorer.score",     # named relative import
        "app.shared.util.deep",     # ../ resolution
        "fs",                       # require, bare
        "app.ats.local",            # require, relative
        "app.ats.util.helper",      # re-export is an import
    })


def test_relative_import_index_collapse_and_root_escape() -> None:
    units = _parse(
        """
        import { x } from "./utils/index";
        import { y } from "../../outside";
        """,
        file_path="src/app.js",
    )
    module = units[0]
    # ./utils/index collapses; ../../ escapes the repo root — kept verbatim.
    assert module.imports == sorted({"src.utils.x", "../../outside.y"})


def test_bare_specifier_subpath_uses_dots() -> None:
    units = _parse('import merge from "lodash/merge";\n')
    assert units[0].imports == ["lodash.merge"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_treesitter_parser.py -v -k "import or specifier"`
Expected: 3 FAIL (imports empty)

- [ ] **Step 3: Implement imports**

In `core/parsing/treesitter_parser.py`, add `import posixpath` to the stdlib imports at the top, then replace the `_extract_imports` stub with:

```python
def _string_value(string_node: Node) -> str:
    for child in string_node.named_children:
        if child.type == "string_fragment":
            return _text(child)
    return _text(string_node).strip("\"'`")


def _normalize_import_source(spec: str, importer_path: str) -> str:
    """Resolve an import specifier to a dotted module reference.

    Relative specifiers resolve against the importing file to a
    repo-relative dotted qname (extension stripped, `index` collapsed).
    Specifiers escaping the repo root are kept verbatim. Bare package
    specifiers keep their name with `/` → `.` (subpath imports).
    """
    if spec.startswith("."):
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(importer_path), spec)
        )
        if resolved.startswith(".."):
            return spec
        return module_qname_from_path(resolved)
    return spec.replace("/", ".")


def _imported_names(node: Node) -> list[str]:
    """Per-symbol names for named imports/re-exports; [] = module-only."""
    names: list[str] = []
    stack = list(node.named_children)
    while stack:
        child = stack.pop()
        if child.type in ("import_specifier", "export_specifier"):
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                names.append(_text(name_node))
        elif child.type in ("import_clause", "named_imports", "export_clause"):
            stack.extend(child.named_children)
    return names


def _extract_imports(root: Node, inputs: _ParseInputs) -> list[str]:
    out: list[str] = []
    for node in root.named_children:
        if node.type in ("import_statement", "export_statement"):
            source = node.child_by_field_name("source")
            if source is None:
                continue  # plain export — not an import
            module = _normalize_import_source(_string_value(source), inputs.file_path)
            names = _imported_names(node)
            if names:
                out.extend(f"{module}.{n}" for n in names)
            else:
                out.append(module)
        elif node.type in _DECL_CONTAINER_TYPES:
            # const x = require("mod") — CommonJS.
            for declarator in node.named_children:
                if declarator.type != "variable_declarator":
                    continue
                value = declarator.child_by_field_name("value")
                if value is None or value.type != "call_expression":
                    continue
                fn = value.child_by_field_name("function")
                if fn is None or _text(fn) != "require":
                    continue
                args = value.child_by_field_name("arguments")
                if args is None:
                    continue
                strings = [c for c in args.named_children if c.type == "string"]
                if len(strings) == 1:
                    out.append(
                        _normalize_import_source(
                            _string_value(strings[0]), inputs.file_path
                        )
                    )
    return out
```

Note: `_emit_declarators` (Task 6) intentionally does not emit a CONSTANT for `const fs = require(...)` bindings unless the name is UPPER_CASE — no change needed there.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_treesitter_parser.py -v`
Expected: ALL 18 PASS

- [ ] **Step 5: Commit**

```bash
git add core/parsing/treesitter_parser.py tests/test_treesitter_parser.py
git commit -m "feat(parsing): JS/TS import extraction with relative-path normalization"
```

---

### Task 9: Pipeline parser registry

**Files:**
- Create: `core/parsing/base.py` (SourceParser protocol)
- Modify: `core/parsing/__init__.py`
- Modify: `core/ingestion/pipeline.py:38-47` and `:134-145`
- Test: `tests/test_pipeline.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py` (reuses its existing `_fake_*_repo` helpers):

```python
def _make_polyglot_repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "tool.py").write_text("def py_fn():\n    return 1\n")
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "app.js").write_text(
        'import { score } from "./scorer";\n'
        "export const run = (x) => score(x);\n"
    )
    (tmp_path / "web" / "scorer.ts").write_text(
        "export function score(x: number): number { return x; }\n"
    )
    (tmp_path / "web" / "types.d.ts").write_text("declare const v: number;\n")
    (tmp_path / "web" / "style.css").write_text("body {}\n")
    return tmp_path


@pytest.mark.asyncio
async def test_pipeline_parses_python_and_js_ts(tmp_path: Path) -> None:
    repo_path = _make_polyglot_repo(tmp_path)
    units_repo = _fake_units_repo()
    captured: list = []
    units_repo.upsert_units = AsyncMock(
        side_effect=lambda units: captured.append(list(units)) or len(list(units))
    )

    ctx = make_context(
        repo_id="r1",
        repo_path=repo_path,
        commit_sha="c1",
        units_collection="repo_r1",
        units_repo=units_repo,
        graph_repo=_fake_graph_repo(),
        vector_repo=_fake_vector_repo(),
    )
    result = await IngestionPipeline().run(ctx)

    # 3 parsed files: tool.py, app.js, scorer.ts (.d.ts + .css skipped).
    assert result.metrics["files_parsed"] == 3
    assert result.failed_files == ()

    qnames = {u.qualified_name for batch in captured for u in batch}
    assert "pkg.tool.py_fn" in qnames
    assert "web.app.run" in qnames
    assert "web.scorer.score" in qnames
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pipeline.py::test_pipeline_parses_python_and_js_ts -v`
Expected: FAIL — `files_parsed == 1` (only the Python file; today's pipeline parses everything with PythonParser but the walker now emits JS/TS FileRefs, which crash PythonParser with SyntaxError → check actual failure mode; either way the assertion fails)

- [ ] **Step 3: Implement the registry**

Create `core/parsing/base.py`:

```python
from __future__ import annotations

from typing import Protocol

from schemas import IngestionUnit


class SourceParser(Protocol):
    """Anything that turns one source file into IngestionUnits.

    Implementations: PythonParser (stdlib ast), TreeSitterParser (JS/TS).
    """

    def parse_file(
        self,
        *,
        source: str,
        repo_id: str,
        file_path: str,
        commit_sha: str,
    ) -> list[IngestionUnit]: ...
```

Update `core/parsing/__init__.py` to:

```python
from core.parsing.base import SourceParser
from core.parsing.file_walker import FileWalker, WalkResult
from core.parsing.python_parser import PythonParser
from core.parsing.qnames import module_qname_from_path
from core.parsing.treesitter_parser import TreeSitterParser

__all__ = [
    "FileWalker",
    "PythonParser",
    "SourceParser",
    "TreeSitterParser",
    "WalkResult",
    "module_qname_from_path",
]
```

In `core/ingestion/pipeline.py`:

1. Update the import (line 12) to:

```python
from core.parsing import FileWalker, PythonParser, SourceParser, TreeSitterParser
```

2. Add `Language` to the schemas import (line 13):

```python
from schemas import FileRef, IngestionUnit, Language, NodeKind
```

3. Add this module-level factory right above the `IngestionPipeline` class:

```python
def _default_parsers() -> dict[Language, SourceParser]:
    return {
        Language.PYTHON: PythonParser(),
        Language.JAVASCRIPT: TreeSitterParser(Language.JAVASCRIPT),
        Language.TYPESCRIPT: TreeSitterParser(Language.TYPESCRIPT),
    }
```

4. Replace `__init__` (lines 38-47) with:

```python
    def __init__(
        self,
        *,
        walker: FileWalker | None = None,
        parsers: dict[Language, SourceParser] | None = None,
        builder: GraphBuilder | None = None,
    ) -> None:
        self._walker = walker or FileWalker()
        self._parsers = parsers if parsers is not None else _default_parsers()
        self._builder = builder or GraphBuilder()
```

(No caller passes the old `parser=` kwarg — verified by grep across `tests/` and `apps/`.)

5. In `_parse_all`, replace the parse block (the `try: units = self._parser.parse_file(...)` section, lines 134-145) with:

```python
            parser = self._parsers.get(file_ref.language)
            if parser is None:
                # Walked but no parser registered — skip silently, same
                # behavior as unknown extensions at the walker level.
                continue
            try:
                units = parser.parse_file(
                    source=source,
                    repo_id=ctx.repo_id,
                    file_path=file_ref.path,
                    commit_sha=ctx.commit_sha,
                )
            except SyntaxError:
                ctx.metrics.files_failed += 1
                failed.append(file_ref.path)
                # parse_file already emitted an error event.
                continue
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py tests/test_golden_pipeline.py -v`
Expected: ALL PASS (golden pipeline tests prove the Python path is unchanged)

- [ ] **Step 5: Commit**

```bash
git add core/parsing/base.py core/parsing/__init__.py core/ingestion/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): per-language parser registry dispatch"
```

---

### Task 10: Graph edges from JS units

Validates the end-to-end promise: JS imports/calls become graph edges, bare packages become EXTERNAL nodes, and same-file call resolution works through the updated `_module_qname`.

**Files:**
- Test: `tests/test_graph_builder.py` (extend)

- [ ] **Step 1: Write the test (expected to pass if Tasks 1-9 are correct — this is an integration pin, not strict TDD)**

Append to `tests/test_graph_builder.py` (match its existing import style; it already imports `GraphBuilder`, `EdgeKind`, `NodeKind`):

```python
def test_js_units_produce_import_call_and_external_edges() -> None:
    from core.parsing import TreeSitterParser
    from schemas import Language

    source = (
        'import { score } from "./scorer";\n'
        'import React from "react";\n'
        "function helper(x) { return x; }\n"
        "export const run = (x) => helper(score(x));\n"
    )
    units = TreeSitterParser(Language.JAVASCRIPT).parse_file(
        source=source,
        repo_id="r1",
        file_path="web/app.js",
        commit_sha="c1",
    )
    result = GraphBuilder().build(units)

    by_kind = {}
    for e in result.edges:
        by_kind.setdefault(e.kind, []).append(e)

    node_by_id = {n.node_id: n for n in result.nodes}

    # Same-file call helper() resolved to the real unit via the
    # `<module>.callee` candidate — this exercises the _module_qname fix.
    helper_unit = next(u for u in units if u.qualified_name == "web.app.helper")
    run_unit = next(u for u in units if u.qualified_name == "web.app.run")
    call_targets = {
        e.dst_id for e in by_kind.get(EdgeKind.CALLS, []) if e.src_id == run_unit.unit_id
    }
    assert helper_unit.unit_id in call_targets

    # Bare package import → External node.
    external_qnames = {
        n.qualified_name for n in result.nodes if n.kind == NodeKind.EXTERNAL
    }
    assert "react" in external_qnames

    # Module unit carries IMPORTS edges.
    module_unit = units[0]
    import_dsts = {
        node_by_id[e.dst_id].qualified_name
        for e in by_kind.get(EdgeKind.IMPORTS, [])
        if e.src_id == module_unit.unit_id
    }
    assert "react" in import_dsts
    assert "web.scorer.score" in import_dsts
```

Check the actual `NodeKind`/`EdgeKind` member names before running — `tests/test_graph_builder.py` already references them; copy its exact spelling (e.g. `NodeKind.EXTERNAL` vs `NodeKind.External`).

- [ ] **Step 2: Run the test**

Run: `.venv/bin/pytest tests/test_graph_builder.py -v`
Expected: ALL PASS. If the new test fails, debug the parser/builder interaction — do NOT weaken the assertions; this test is the core promise of the feature.

- [ ] **Step 3: Commit**

```bash
git add tests/test_graph_builder.py
git commit -m "test(graph): pin JS import/call/external edge construction"
```

---

### Task 11: Full verification — suite, lint, Docker build

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/pytest tests/ -x -q`
Expected: ALL PASS, no skips beyond the pre-existing integration markers

- [ ] **Step 2: Lint + type-check (match repo tooling)**

```bash
.venv/bin/ruff check core/ schemas/ tests/
.venv/bin/mypy core/parsing/ core/ingestion/
```

Expected: clean. (If `mypy` flags the `tree_sitter` stubs, add `tree_sitter_javascript`/`tree_sitter_typescript` to the existing ignore-missing-imports section in `pyproject.toml` — check what's already there for precedent.)

- [ ] **Step 3: Production Docker build (the B8/B9 gate)**

```bash
docker build -f Dockerfile.production -t memory-cl:multilang-test .
docker run --rm --entrypoint python memory-cl:multilang-test -c "
from core.parsing import TreeSitterParser
from schemas import Language
units = TreeSitterParser(Language.JAVASCRIPT).parse_file(
    source='const f = (x) => x;', repo_id='t', file_path='a.js', commit_sha='c')
print('container parse ok:', len(units), 'units')
"
```

Expected: build succeeds; container prints `container parse ok: 2 units`

- [ ] **Step 4: Push branch**

```bash
git push -u origin feat/multilang-ingestion-js-ts
```

Then tell the user the branch is ready for PR + merge via GitHub UI (their workflow), and that post-merge VM validation is: `git pull`, `build --no-cache api` (lockfile changed), `up -d`, re-ingest JA4M, confirm `.cjs`/`.js` files now produce units and `query_graph` works on a JS qname.

---

## Post-plan notes for the executor

- The venv already has the three tree-sitter packages installed (plan research did it). Task 4 Step 3 is idempotent.
- All grammar node-type facts in "Verified grammar facts" were probed against the exact pinned versions — trust them over intuition.
- If a test assertion fails on an exact string (e.g. signature spacing), print the actual unit and check whether the expectation or the extraction is wrong before changing either. The determinism contract (sorted, deduped) comes from the `IngestionUnit` validator — don't re-sort in the parser except the `(line_start, name)` child sort.
- Do NOT touch `apps/`, `storage/`, or `ui/` — this feature is parser + pipeline only.
