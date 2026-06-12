from __future__ import annotations

import posixpath
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
_CLASS_DECL_TYPES = frozenset({
    "class_declaration",
    "abstract_class_declaration",  # TS grammar — `abstract class A {}`
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


# ---------------------------------------------------------------------------
# Extractors — filled in by subsequent tasks
# ---------------------------------------------------------------------------
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
        elif decl.type in _CLASS_DECL_TYPES:
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
                # Grammar asymmetry: TS's public_field_definition exposes the
                # field name under `name`; JS's field_definition exposes it
                # under `property` (which also covers #private names).
                fname_node = member.child_by_field_name("name") or member.child_by_field_name("property")
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
        elif node.type == "new_expression":
            target = _member_chain(node.child_by_field_name("constructor"))
            if target:
                calls.append(target)
        elif node.type == "identifier":
            references.append(_text(node))
        stack.extend(node.named_children)
    return calls, references


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
    specifiers keep their name with `/` -> `.` (subpath imports).
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
            if not module or module == ".":
                continue  # degenerate specifier ("" or bare "../") — no graph value
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
                    module = _normalize_import_source(
                        _string_value(strings[0]), inputs.file_path
                    )
                    if module and module != ".":
                        out.append(module)
    return out


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
