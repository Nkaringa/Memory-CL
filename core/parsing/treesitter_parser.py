from __future__ import annotations

import time
from functools import lru_cache
from typing import Protocol

import tree_sitter_c_sharp as _tscsharp
import tree_sitter_go as _tsgo
import tree_sitter_java as _tsjava
import tree_sitter_javascript as _tsjs
import tree_sitter_rust as _tsrust
import tree_sitter_typescript as _tsts
from tree_sitter import Language as _TSLanguage
from tree_sitter import Node
from tree_sitter import Parser as _TSParser

from core.ingestion.logevent import emit_phase2_event
from core.observability import get_tracer
from core.parsing.languages import csharp, go, java, javascript, rust
from core.parsing.languages._shared import _make_unit, _ParseInputs
from core.parsing.qnames import module_qname_from_path
from schemas import (
    IngestionUnit,
    Language,
    UnitKind,
    content_sha,
)

_tracer = get_tracer("core.parsing.treesitter_parser")


# ---------------------------------------------------------------------------
# Grammar plumbing
# ---------------------------------------------------------------------------
@lru_cache(maxsize=8)
def _grammar_parser(grammar: str) -> _TSParser:
    """One Parser per grammar, shared process-wide (parsers are reusable)."""
    if grammar == "javascript":
        lang = _TSLanguage(_tsjs.language())
    elif grammar == "tsx":
        lang = _TSLanguage(_tsts.language_tsx())
    elif grammar == "csharp":
        lang = _TSLanguage(_tscsharp.language())
    elif grammar == "go":
        lang = _TSLanguage(_tsgo.language())
    elif grammar == "java":
        lang = _TSLanguage(_tsjava.language())
    elif grammar == "rust":
        lang = _TSLanguage(_tsrust.language())
    else:
        lang = _TSLanguage(_tsts.language_typescript())
    return _TSParser(lang)


def _grammar_for(language: Language, file_path: str) -> str:
    if language is Language.JAVASCRIPT:
        return "javascript"  # the JS grammar includes JSX
    if language is Language.TYPESCRIPT:
        return "tsx" if file_path.endswith(".tsx") else "typescript"
    # Batch-2 languages map 1:1 onto their grammar.
    return language.value


# ---------------------------------------------------------------------------
# Per-language extractor dispatch
# ---------------------------------------------------------------------------
class _LanguageExtractor(Protocol):
    """Public interface every `core.parsing.languages.<lang>` module implements.

    Implementations are *modules* — mypy drops `self` when matching a
    module against a protocol method.
    """

    def extract_children(self, root: Node, inputs: _ParseInputs) -> list[IngestionUnit]: ...

    def extract_imports(self, root: Node, inputs: _ParseInputs) -> list[str]: ...

    def module_docstring(self, root: Node) -> str | None: ...


_EXTRACTORS: dict[Language, _LanguageExtractor] = {
    Language.JAVASCRIPT: javascript,
    Language.TYPESCRIPT: javascript,  # same node shapes, grammar-switched above
    Language.CSHARP: csharp,
    Language.GO: go,
    Language.JAVA: java,
    Language.RUST: rust,
}


def _count_error_nodes(root: Node) -> int:
    count = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            count += 1
        stack.extend(node.children)
    return count


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
class TreeSitterParser:
    """Convert tree-sitter-backed sources into deterministic `IngestionUnit`s.

    Thin dispatcher: grammar selection + module-unit emission live here;
    per-language extraction rules live in `core.parsing.languages.<lang>`.

    Same contract as `PythonParser.parse_file`: module unit first,
    children sorted by `(line_start, name)`. Tree-sitter is
    error-tolerant — files with syntax errors still yield whatever
    units parsed cleanly, with a `parse_partial` event emitted.
    """

    def __init__(self, language: Language) -> None:
        if language not in _EXTRACTORS:
            raise ValueError(f"TreeSitterParser does not handle {language}")
        self._language = language
        self._extractor = _EXTRACTORS[language]

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
                    error_nodes=_count_error_nodes(root),
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

            children = self._extractor.extract_children(root, inputs)
            module_unit = _make_unit(
                inputs=inputs,
                kind=UnitKind.MODULE,
                name=module_qname.split(".")[-1] if module_qname else file_path,
                qualified_name=module_qname,
                parent_qualified_name=None,
                line_start=1,
                line_end=max(1, source.count("\n") + 1),
                content=source,
                docstring=self._extractor.module_docstring(root),
                signature=None,
                imports=self._extractor.extract_imports(root, inputs),
                calls=[],
                references=[],
                bases=[],
            )

            children.sort(key=lambda u: (u.line_start, u.name))
            units = [module_unit, *children]

            duration = (time.perf_counter() - start) * 1000
            final_status = "partial" if root.has_error else "success"
            emit_phase2_event(
                event="parse_ok",
                operation="treesitter_parser.parse_file",
                status=final_status,
                duration_ms=duration,
                file_path=file_path,
                content_hash=content_sha(source),
                level="debug",
                units_emitted=len(units),
            )
            span.set_attribute("units_emitted", len(units))
            return units
