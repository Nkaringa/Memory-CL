"""Rust extraction rules (multilang batch-2, Task 5).

Verified grammar facts (tree-sitter-rust 0.24.2, probed empirically):

- ``function_item``: fields ``name`` (identifier), ``type_parameters``,
  ``parameters``, ``return_type``, ``body``. ``async`` is NOT a direct
  child — it lives inside a ``function_modifiers`` child node.
- ``struct_item``/``enum_item``/``trait_item``/``union_item``: fields
  ``name`` (type_identifier) + ``body``. Trait bodies are
  ``declaration_list``s containing ``function_item`` (default methods,
  emitted as METHOD), ``function_signature_item`` (body-less required
  methods — SKIPPED, no executable content) and ``const_item``.
- ``impl_item``: fields ``type`` (type_identifier | generic_type |
  scoped_type_identifier), optional ``trait`` (same shapes) and
  ``body`` (declaration_list). Inherent ``impl Point`` has no ``trait``
  field; ``impl Area for Point`` has both. Methods/consts inside are
  parented on ``<module>.<TypeName>``; the trait of a trait impl is
  merged into the bases of the type's CLASS unit when that type is
  declared in the same scope of the same file (best-effort).
- ``const_item``/``static_item``: fields ``name``, ``type``, ``value``.
  ALL of them become CONSTANT — the keyword is explicit, so the
  UPPER_CASE name heuristic is unnecessary.
- ``mod_item``: fields ``name`` + ``body`` for inline ``mod x { ... }``
  blocks (descended one level, qnames nest); declaration-only
  ``mod x;`` has no ``body`` field and is skipped.
- ``use_declaration``: field ``argument`` -> identifier |
  scoped_identifier(path, name) | scoped_use_list(path, list->use_list)
  | use_list | use_wildcard (path as sole named child) |
  use_as_clause(path, alias). ``::`` becomes ``.``; aliases record the
  original path; ``{self, X}`` records the prefix itself for ``self``.
  Only top-level use declarations are collected (imports inside inline
  mods are skipped, same as every other language module).
- Doc comments are token-level ``line_comment`` nodes with marker
  fields ``outer`` (``///``) / ``inner`` (``//!``) and the prose under
  field ``doc`` (marker already stripped). A doc line_comment's span
  includes its trailing newline, so ``comment.end_point.row ==
  item.start_point.row`` is the adjacency test. ``attribute_item``
  nodes (``#[derive(...)]``) sit BETWEEN the doc run and the item and
  are skipped while collecting. Block doc comments (``/** */``) are
  not handled.
- ``call_expression``: field ``function`` -> identifier |
  scoped_identifier (``a::b::f`` -> "a.b.f"; turbofish
  ``Vec::<u8>::new`` has a generic_type as the path — unwrapped via its
  ``type`` field) | field_expression(value, field) (``x.f()`` ->
  "x.f"). Unresolvable shapes (index/parenthesized/call results)
  return None and are skipped.
- ``macro_invocation`` (``println!``, ``vec!``, ...): SKIPPED as calls.
  Recording macro names would be dominated by println/format/vec noise,
  and ``token_tree`` contents are raw tokens, never call_expressions.
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

_TYPE_DECL_TYPES = frozenset({
    "struct_item",
    "enum_item",
    "trait_item",
    "union_item",
})
_CONST_DECL_TYPES = frozenset({
    "const_item",
    "static_item",
})


# ---------------------------------------------------------------------------
# Doc comments
# ---------------------------------------------------------------------------
def _doc_lines(comment: Node) -> str | None:
    doc = comment.child_by_field_name("doc")
    return _text(doc).strip() if doc is not None else None


def module_docstring(root: Node) -> str | None:
    """Leading run of inner doc comments (`//!`) at the top of the file."""
    lines: list[str] = []
    for child in root.named_children:
        if child.type == "line_comment" and child.child_by_field_name("inner") is not None:
            line = _doc_lines(child)
            if line is not None:
                lines.append(line)
                continue
        break
    cleaned = "\n".join(ln for ln in lines if ln)
    return cleaned or None


def _doc_for(node: Node) -> str | None:
    """Contiguous `///` run directly above `node`, cleaned.

    Skips `#[attribute]` items sitting between the docs and the item.
    A blank line breaks the run (doc line_comments span through their
    trailing newline, hence the end-row adjacency test).
    """
    lines: list[str] = []
    expected_row = node.start_point.row
    cur = node.prev_named_sibling
    while cur is not None:
        if cur.type == "attribute_item" and not lines:
            # Attributes span up to (not through) their newline.
            if cur.end_point.row != expected_row - 1:
                break
            expected_row = cur.start_point.row
            cur = cur.prev_named_sibling
            continue
        if cur.type != "line_comment" or cur.child_by_field_name("outer") is None:
            break
        # Doc line_comments span THROUGH their trailing newline, so a
        # directly-adjacent one ends exactly on the item's start row.
        if cur.end_point.row != expected_row:
            break
        line = _doc_lines(cur)
        if line is None:
            break
        lines.append(line)
        expected_row = cur.start_point.row
        cur = cur.prev_named_sibling
    cleaned = "\n".join(ln for ln in reversed(lines) if ln)
    return cleaned or None


# ---------------------------------------------------------------------------
# Path / chain reconstruction
# ---------------------------------------------------------------------------
def _type_path(node: Node | None) -> str | None:
    """Dotted name for a `::`-separated path or type reference."""
    if node is None:
        return None
    if node.type == "generic_type":
        return _type_path(node.child_by_field_name("type"))
    if node.type in ("identifier", "type_identifier", "crate", "super", "self"):
        return _text(node)
    if node.type in ("scoped_identifier", "scoped_type_identifier"):
        name_node = node.child_by_field_name("name")
        right = _text(name_node) if name_node is not None else None
        left = _type_path(node.child_by_field_name("path"))
        if left and right:
            return f"{left}.{right}"
        return right or left
    return None


def _field_chain(node: Node) -> str | None:
    """`x.f`, `self.a.b` field_expression chains -> dotted name."""
    parts: list[str] = []
    cur: Node | None = node
    while cur is not None and cur.type == "field_expression":
        field = cur.child_by_field_name("field")
        if field is None:
            return None
        parts.append(_text(field))
        cur = cur.child_by_field_name("value")
    if cur is not None and cur.type in ("identifier", "self"):
        parts.append(_text(cur))
        return ".".join(reversed(parts))
    return None  # value is a call result / index / paren — unresolvable


def _call_target(fn: Node | None) -> str | None:
    if fn is None:
        return None
    if fn.type == "identifier":
        return _text(fn)
    if fn.type == "scoped_identifier":
        return _type_path(fn)
    if fn.type == "field_expression":
        return _field_chain(fn)
    return None


def _walk_body(fn: Node) -> tuple[list[str], list[str]]:
    """Collect call targets + identifier references in a function body.

    Nested closures are not separate units, so their calls attribute to
    the enclosing function (same contract as the JS/Python parsers).
    Macro invocations are skipped as calls (see module docstring).
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
            target = _call_target(node.child_by_field_name("function"))
            if target:
                calls.append(target)
        elif node.type == "identifier":
            references.append(_text(node))
        stack.extend(node.named_children)
    return calls, references


# ---------------------------------------------------------------------------
# Unit emission
# ---------------------------------------------------------------------------
def _is_async_fn(fn: Node) -> bool:
    return any(
        child.type == "function_modifiers"
        and any(g.type == "async" for g in child.children)
        for child in fn.children
    )


def _fn_signature(name: str, fn: Node) -> str:
    """`[async ]fn name<T>(params) -> Ret`, reconstructed from fields."""
    type_params = fn.child_by_field_name("type_parameters")
    tp_text = _text(type_params) if type_params is not None else ""
    params = fn.child_by_field_name("parameters")
    params_text = _text(params) if params is not None else "()"
    ret = fn.child_by_field_name("return_type")
    ret_text = f" -> {_text(ret)}" if ret is not None else ""
    prefix = "async " if _is_async_fn(fn) else ""
    return f"{prefix}fn {name}{tp_text}{params_text}{ret_text}"


def _emit_function(
    fn: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    kind: UnitKind,
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
        docstring=_doc_for(fn),
        signature=_fn_signature(name, fn),
        imports=[],
        calls=calls,
        references=references,
        bases=[],
    )


def _emit_constant(
    decl: Node,
    inputs: _ParseInputs,
    parent_qname: str,
) -> IngestionUnit | None:
    name_node = decl.child_by_field_name("name")
    if name_node is None:
        return None
    name = _text(name_node)
    line_start = decl.start_point.row + 1
    line_end = decl.end_point.row + 1
    return _make_unit(
        inputs=inputs,
        kind=UnitKind.CONSTANT,
        name=name,
        qualified_name=f"{parent_qname}.{name}" if parent_qname else name,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=_doc_for(decl),
        signature=None,
        imports=[],
        calls=[],
        references=[],
        bases=[],
    )


def _emit_type_decl(
    decl: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    bases: list[str],
) -> list[IngestionUnit]:
    name_node = decl.child_by_field_name("name")
    if name_node is None:
        return []
    name = _text(name_node)
    qname = f"{parent_qname}.{name}" if parent_qname else name

    children: list[IngestionUnit] = []
    if decl.type == "trait_item":
        # Default methods + associated consts. Required (body-less)
        # function_signature_items are skipped — no executable content.
        body = decl.child_by_field_name("body")
        if body is not None:
            children = _emit_members(body, inputs, qname)

    line_start = decl.start_point.row + 1
    line_end = decl.end_point.row + 1
    unit = _make_unit(
        inputs=inputs,
        kind=UnitKind.CLASS,
        name=name,
        qualified_name=qname,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=_doc_for(decl),
        signature=None,
        imports=[],
        calls=[],
        references=[],
        bases=bases,
    )
    return [unit, *children]


def _impl_type_name(impl: Node) -> str | None:
    """Bare name of the impl'd type (`impl Container<T>` -> Container)."""
    type_node = impl.child_by_field_name("type")
    if type_node is None:
        return None
    path = _type_path(type_node)
    return path.split(".")[-1] if path else None


def _emit_members(
    body: Node,
    inputs: _ParseInputs,
    parent_qname: str,
) -> list[IngestionUnit]:
    """METHOD/CONSTANT units inside an impl/trait declaration_list."""
    out: list[IngestionUnit] = []
    for member in body.named_children:
        if member.type == "function_item":
            unit = _emit_function(member, inputs, parent_qname, kind=UnitKind.METHOD)
            if unit is not None:
                out.append(unit)
        elif member.type in _CONST_DECL_TYPES:
            unit = _emit_constant(member, inputs, parent_qname)
            if unit is not None:
                out.append(unit)
    return out


def _extract_scope(
    container: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    depth: int,
) -> list[IngestionUnit]:
    # Pass 1: trait impls in this scope -> bases for the impl'd types,
    # merged into the type's CLASS unit when it is declared right here.
    trait_bases: dict[str, list[str]] = {}
    for node in container.named_children:
        if node.type != "impl_item":
            continue
        trait_node = node.child_by_field_name("trait")
        if trait_node is None:
            continue  # inherent impl
        type_name = _impl_type_name(node)
        trait_path = _type_path(trait_node)
        if type_name and trait_path:
            trait_bases.setdefault(type_name, []).append(trait_path)

    # Pass 2: emit units.
    out: list[IngestionUnit] = []
    for node in container.named_children:
        if node.type == "function_item":
            unit = _emit_function(node, inputs, parent_qname, kind=UnitKind.FUNCTION)
            if unit is not None:
                out.append(unit)
        elif node.type in _TYPE_DECL_TYPES:
            name_node = node.child_by_field_name("name")
            bases = trait_bases.get(_text(name_node), []) if name_node is not None else []
            out.extend(_emit_type_decl(node, inputs, parent_qname, bases))
        elif node.type in _CONST_DECL_TYPES:
            unit = _emit_constant(node, inputs, parent_qname)
            if unit is not None:
                out.append(unit)
        elif node.type == "impl_item":
            type_name = _impl_type_name(node)
            body = node.child_by_field_name("body")
            if type_name and body is not None:
                type_qname = f"{parent_qname}.{type_name}" if parent_qname else type_name
                out.extend(_emit_members(body, inputs, type_qname))
        elif node.type == "mod_item" and depth == 0:
            # Inline `mod x { ... }` — descend one level, qnames nest.
            # Declaration-only `mod x;` has no body field and is skipped.
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node is not None and body is not None:
                mod_qname = (
                    f"{parent_qname}.{_text(name_node)}" if parent_qname else _text(name_node)
                )
                out.extend(_extract_scope(body, inputs, mod_qname, depth=depth + 1))
    return out


def extract_children(root: Node, inputs: _ParseInputs) -> list[IngestionUnit]:
    return _extract_scope(root, inputs, inputs.module_qname, depth=0)


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
def _join(prefix: str, segment: str) -> str:
    return f"{prefix}.{segment}" if prefix else segment


def _use_paths(node: Node, prefix: str) -> list[str]:
    """Flatten a use_declaration argument into dotted import paths."""
    if node.type == "self":
        # `use std::io::{self, Read}` — `self` imports the prefix itself.
        return [prefix] if prefix else []
    if node.type in ("identifier", "scoped_identifier", "crate", "super"):
        path = _type_path(node)
        return [_join(prefix, path)] if path else []
    if node.type == "use_as_clause":
        # Alias records the ORIGINAL path, not the local rename.
        path_node = node.child_by_field_name("path")
        return _use_paths(path_node, prefix) if path_node is not None else []
    if node.type == "use_wildcard":
        # `use a::b::*` -> "a.b" (the glob'd module itself).
        inner = next(iter(node.named_children), None)
        if inner is not None:
            return _use_paths(inner, prefix)
        return [prefix] if prefix else []
    if node.type == "scoped_use_list":
        path_node = node.child_by_field_name("path")
        list_node = node.child_by_field_name("list")
        base = _type_path(path_node)
        new_prefix = _join(prefix, base) if base else prefix
        return _use_paths(list_node, new_prefix) if list_node is not None else []
    if node.type == "use_list":
        out: list[str] = []
        for child in node.named_children:
            out.extend(_use_paths(child, prefix))
        return out
    return []


def extract_imports(root: Node, inputs: _ParseInputs) -> list[str]:
    out: list[str] = []
    for node in root.named_children:
        if node.type != "use_declaration":
            continue
        argument = node.child_by_field_name("argument")
        if argument is not None:
            out.extend(_use_paths(argument, ""))
    return out
