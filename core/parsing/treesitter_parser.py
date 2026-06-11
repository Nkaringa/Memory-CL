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
