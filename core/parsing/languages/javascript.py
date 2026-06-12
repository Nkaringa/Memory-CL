"""JS/TS extraction rules — relocated verbatim from treesitter_parser.py.

Handles the `javascript`, `typescript`, and `tsx` grammars (the
dispatcher picks the grammar; the extraction rules below cover the node
shapes of all three). Behavior contract: tests/test_treesitter_parser.py
passes unmodified.
"""

from __future__ import annotations

import posixpath

from tree_sitter import Node

from core.parsing.languages._shared import (
    _clean_block_comment,
    _is_constant_name,
    _jsdoc_for,
    _make_unit,
    _member_chain,
    _ParseInputs,
    _signature,
    _slice_source,
    _text,
)
from core.parsing.qnames import module_qname_from_path
from schemas import IngestionUnit, UnitKind

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


def module_docstring(root: Node) -> str | None:
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


def extract_children(root: Node, inputs: _ParseInputs) -> list[IngestionUnit]:
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


def extract_imports(root: Node, inputs: _ParseInputs) -> list[str]:
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
