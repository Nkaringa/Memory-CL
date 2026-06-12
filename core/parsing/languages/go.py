"""Go extraction rules (multilang batch-2 Task 3).

Node/field names verified empirically against ``tree_sitter_go`` 0.25.0:

- ``package_clause`` > ``package_identifier`` (no field). Doc comments are
  sibling ``comment`` nodes — one node per ``//`` line — directly above.
- ``function_declaration``: fields ``name`` (identifier),
  ``type_parameters`` (type_parameter_list, generics), ``parameters``
  (parameter_list), ``result`` (bare type OR parameter_list), ``body``.
- ``method_declaration``: same fields plus ``receiver`` (parameter_list
  holding one parameter_declaration whose ``type`` field is
  type_identifier | pointer_type | generic_type; pointer_type wraps the
  base type as its only *named* child, no field; generic_type exposes
  the base under field ``type``). ``name`` is a field_identifier.
- ``type_declaration``: children are ``type_spec`` nodes (fields ``name``
  type_identifier, ``type``), grouped form adds ``(`` / ``)`` anonymous
  children with per-spec ``comment`` siblings inside. ``type X = Y`` is a
  distinct ``type_alias`` node (skipped). struct_type >
  field_declaration_list > field_declaration; embedded fields have NO
  ``name`` field — their ``type`` field (type_identifier | qualified_type
  | generic_type, optionally behind a ``*``/pointer) is the base.
  interface_type embeds via ``type_elem`` children (qualified_type /
  type_identifier); methods are ``method_elem`` (not extracted as units).
- ``const_declaration`` / ``var_declaration``: ``const_spec``/``var_spec``
  children; a spec can carry SEVERAL ``name`` fields (``A, B = 1, 2`` —
  the separating comma is also tagged ``name``, so filter on
  ``identifier``). ``iota`` specs simply have no ``value``.
- ``import_declaration``: ``import_spec`` direct child (single form) or
  inside ``import_spec_list`` (grouped). Fields: ``path``
  (interpreted_string_literal) and optional ``name`` (package_identifier
  alias | blank_identifier ``_`` | dot ``.``).
- ``call_expression``: field ``function`` is identifier |
  selector_expression (fields ``operand``/``field``) | call_expression |
  index_expression | ... — only the first two resolve to dotted names.

Mapping (plan row "Go"): function_declaration -> FUNCTION;
method_declaration -> METHOD parented on ``<module>.<ReceiverType>``
(pointer stripped); type_declaration with struct/interface -> CLASS
(embedded types -> bases, best-effort); EVERY top-level const spec ->
CONSTANT (Go convention is CamelCase — the UPPER rule is deliberately
NOT applied); plain type aliases and ``var`` declarations are skipped.
Imports record the import *path* dotted ("net/http" -> "net.http"),
never the alias; blank (``_``) and dot imports are included.
Docstrings are contiguous ``//`` runs immediately above a declaration
(no blank line in between), cleaned of the ``//`` prefix.
"""

from __future__ import annotations

from tree_sitter import Node

from core.parsing.languages._shared import (
    _make_unit,
    _ParseInputs,
    _slice_source,
    _text,
)
from schemas import IngestionUnit, UnitKind

_CLASSY_TYPE_NODES = frozenset({"struct_type", "interface_type"})


# ---------------------------------------------------------------------------
# Doc comments
# ---------------------------------------------------------------------------
def _clean_line_comments(comment_texts: list[str]) -> str | None:
    """Strip `//` decoration from an ordered run of line comments."""
    lines: list[str] = []
    for text in comment_texts:
        body = text[2:] if text.startswith("//") else text
        if body.startswith(" "):
            body = body[1:]
        lines.append(body.rstrip())
    cleaned = "\n".join(ln for ln in lines if ln)
    return cleaned or None


def _doc_for(node: Node) -> str | None:
    """Contiguous `//` comment run IMMEDIATELY above `node` (no blank line).

    Works both at file top level and inside grouped `const (...)` /
    `type (...)` blocks — comments are named siblings in both scopes.
    Each `//` comment is its own single-row node, so contiguity is a
    row-adjacency walk upward. A comment trailing other code on its own
    line (`X = 1 // note`) is NOT a doc for the next declaration.
    """
    comments: list[str] = []
    expected_row = node.start_point.row - 1
    cur = node.prev_named_sibling
    while (
        cur is not None
        and cur.type == "comment"
        and cur.end_point.row == expected_row
        and _text(cur).startswith("//")
    ):
        before = cur.prev_sibling
        if before is not None and before.end_point.row == cur.start_point.row:
            break  # trailing comment after code on the same line
        comments.append(_text(cur))
        expected_row = cur.start_point.row - 1
        cur = cur.prev_named_sibling
    if not comments:
        return None
    return _clean_line_comments(list(reversed(comments)))


def module_docstring(root: Node) -> str | None:
    """The package doc: `//` run directly above the package_clause."""
    for child in root.named_children:
        if child.type == "package_clause":
            return _doc_for(child)
    return None


# ---------------------------------------------------------------------------
# Names / types / signatures
# ---------------------------------------------------------------------------
def _type_base_name(type_node: Node | None) -> str | None:
    """Base name of a type, pointer `*` stripped, generics reduced.

    `*Handler` -> `Handler`, `Pair[T]` -> `Pair`, `http.Client` ->
    `http.Client`. Unresolvable shapes return None.
    """
    if type_node is None:
        return None
    if type_node.type == "pointer_type":
        inner = next(iter(type_node.named_children), None)
        return _type_base_name(inner)
    if type_node.type == "generic_type":
        return _type_base_name(type_node.child_by_field_name("type"))
    if type_node.type in ("type_identifier", "qualified_type"):
        return _text(type_node)
    return None


def _receiver_type_name(method: Node) -> str | None:
    recv = method.child_by_field_name("receiver")
    if recv is None:
        return None
    for pd in recv.named_children:
        if pd.type == "parameter_declaration":
            return _type_base_name(pd.child_by_field_name("type"))
    return None


def _go_signature(fn: Node, name: str) -> str:
    """Go-style `func [recv] name[tparams](params) results` from field text."""
    recv = fn.child_by_field_name("receiver")
    recv_text = f"{_text(recv)} " if recv is not None else ""
    tparams = fn.child_by_field_name("type_parameters")
    tp_text = _text(tparams) if tparams is not None else ""
    params = fn.child_by_field_name("parameters")
    params_text = _text(params) if params is not None else "()"
    result = fn.child_by_field_name("result")
    result_text = f" {_text(result)}" if result is not None else ""
    return f"func {recv_text}{name}{tp_text}{params_text}{result_text}"


def _selector_chain(node: Node | None) -> str | None:
    """selector_expression/identifier -> dotted name; else None (skipped)."""
    if node is None:
        return None
    parts: list[str] = []
    cur: Node | None = node
    while cur is not None and cur.type == "selector_expression":
        field = cur.child_by_field_name("field")
        if field is None:
            return None
        parts.append(_text(field))
        cur = cur.child_by_field_name("operand")
    if cur is not None and cur.type == "identifier":
        parts.append(_text(cur))
        return ".".join(reversed(parts))
    return None


def _walk_body(fn: Node) -> tuple[list[str], list[str]]:
    """Call targets + identifier references in a function/method body."""
    calls: list[str] = []
    references: list[str] = []
    body = fn.child_by_field_name("body")
    if body is None:
        return calls, references
    stack: list[Node] = [body]
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            target = _selector_chain(node.child_by_field_name("function"))
            if target:
                calls.append(target)
        elif node.type == "identifier":
            references.append(_text(node))
        stack.extend(node.named_children)
    return calls, references


# ---------------------------------------------------------------------------
# Unit emission
# ---------------------------------------------------------------------------
def _emit_function(
    fn: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    kind: UnitKind,
    docstring: str | None,
) -> IngestionUnit | None:
    name_node = fn.child_by_field_name("name")
    if name_node is None:
        return None
    name = _text(name_node)
    qname = f"{parent_qname}.{name}" if parent_qname else name
    line_start = fn.start_point.row + 1
    line_end = fn.end_point.row + 1
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
        signature=_go_signature(fn, name),
        imports=[],
        calls=calls,
        references=references,
        bases=[],
    )


def _struct_bases(struct_type: Node) -> list[str]:
    """Embedded fields (no `name` field) -> bases, best-effort."""
    bases: list[str] = []
    field_list = next(
        (c for c in struct_type.named_children if c.type == "field_declaration_list"),
        None,
    )
    if field_list is None:
        return bases
    for fd in field_list.named_children:
        if fd.type != "field_declaration":
            continue
        if fd.child_by_field_name("name") is not None:
            continue  # named field, not embedding
        base = _type_base_name(fd.child_by_field_name("type"))
        if base:
            bases.append(base)
    return bases


def _interface_bases(interface_type: Node) -> list[str]:
    """Embedded interfaces (`type_elem` children) -> bases, best-effort."""
    bases: list[str] = []
    for elem in interface_type.named_children:
        if elem.type != "type_elem":
            continue
        inner = next(iter(elem.named_children), None)
        base = _type_base_name(inner) if inner is not None else None
        bases.append(base if base else _text(elem).strip())
    return bases


def _emit_type_spec(
    spec: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    docstring: str | None,
) -> IngestionUnit | None:
    name_node = spec.child_by_field_name("name")
    type_node = spec.child_by_field_name("type")
    if name_node is None or type_node is None:
        return None
    if type_node.type not in _CLASSY_TYPE_NODES:
        return None  # plain type alias / defined non-composite type — skipped
    name = _text(name_node)
    qname = f"{parent_qname}.{name}" if parent_qname else name
    if type_node.type == "struct_type":
        bases = _struct_bases(type_node)
    else:
        bases = _interface_bases(type_node)
    line_start = spec.start_point.row + 1
    line_end = spec.end_point.row + 1
    return _make_unit(
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


def _emit_const_spec(
    spec: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    docstring: str | None,
) -> list[IngestionUnit]:
    """One CONSTANT per name in the spec (`A, B = 1, 2` -> two units).

    NO UPPER_CASE filter: Go constants are conventionally CamelCase, so
    every top-level const is a CONSTANT (plan: relaxed rule).
    """
    out: list[IngestionUnit] = []
    names = [
        c for c in spec.children_by_field_name("name") if c.type == "identifier"
    ]
    line_start = spec.start_point.row + 1
    line_end = spec.end_point.row + 1
    for name_node in names:
        name = _text(name_node)
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
                docstring=docstring,
                signature=None,
                imports=[],
                calls=[],
                references=[],
                bases=[],
            )
        )
    return out


def _is_grouped(decl: Node) -> bool:
    """Grouped `const (...)` / `type (...)` form vs single-spec form."""
    return any(c.type == "(" for c in decl.children)


def _spec_doc(spec: Node, decl: Node) -> str | None:
    """Doc for a spec: its own `//` run inside a group, else (single-spec
    form only) the run above the enclosing declaration."""
    doc = _doc_for(spec)
    if doc is None and not _is_grouped(decl):
        doc = _doc_for(decl)
    return doc


def extract_children(root: Node, inputs: _ParseInputs) -> list[IngestionUnit]:
    out: list[IngestionUnit] = []
    parent = inputs.module_qname
    for node in root.named_children:
        if node.type == "function_declaration":
            unit = _emit_function(
                node, inputs, parent, kind=UnitKind.FUNCTION, docstring=_doc_for(node)
            )
            if unit is not None:
                out.append(unit)
        elif node.type == "method_declaration":
            recv_type = _receiver_type_name(node)
            if recv_type is not None:
                # Pointer `*` already stripped; if the receiver type is
                # declared in this file the parent qname resolves in graph.
                method_parent = f"{parent}.{recv_type}" if parent else recv_type
                unit = _emit_function(
                    node,
                    inputs,
                    method_parent,
                    kind=UnitKind.METHOD,
                    docstring=_doc_for(node),
                )
            else:
                unit = _emit_function(
                    node, inputs, parent, kind=UnitKind.FUNCTION, docstring=_doc_for(node)
                )
            if unit is not None:
                out.append(unit)
        elif node.type == "type_declaration":
            for spec in node.named_children:
                if spec.type != "type_spec":
                    continue  # type_alias / comments — skipped
                unit = _emit_type_spec(
                    spec, inputs, parent, docstring=_spec_doc(spec, node)
                )
                if unit is not None:
                    out.append(unit)
        elif node.type == "const_declaration":
            for spec in node.named_children:
                if spec.type != "const_spec":
                    continue
                out.extend(
                    _emit_const_spec(
                        spec, inputs, parent, docstring=_spec_doc(spec, node)
                    )
                )
    return out


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
def _import_path(spec: Node) -> str | None:
    path_node = spec.child_by_field_name("path")
    if path_node is None:
        return None
    path = _text(path_node).strip('"`')
    return path or None


def extract_imports(root: Node, inputs: _ParseInputs) -> list[str]:
    """Import paths as dotted refs ("net/http" -> "net.http").

    The path is recorded even when aliased (the alias is local naming
    only); blank `_` and dot `.` imports are included too — they are
    real dependency edges.
    """
    out: list[str] = []
    for node in root.named_children:
        if node.type != "import_declaration":
            continue
        stack = [c for c in node.named_children]
        for child in stack:
            specs = (
                child.named_children if child.type == "import_spec_list" else [child]
            )
            for spec in specs:
                if spec.type != "import_spec":
                    continue
                path = _import_path(spec)
                if path:
                    out.append(path.replace("/", "."))
    return out
