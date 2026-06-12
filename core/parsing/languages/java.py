"""Java extraction rules (multilang batch-2 Task 4).

Verified grammar facts (tree-sitter-java 0.23.5, probed empirically):

- Top level: ``program`` -> ``package_declaration`` (named child
  ``scoped_identifier``), ``import_declaration``, type declarations, and
  ``block_comment``/``line_comment`` siblings (NOT ``comment`` — so the
  shared ``_jsdoc_for`` does not apply; Java has its own ``_javadoc_for``).
- Type declarations: ``class_declaration``, ``interface_declaration``,
  ``enum_declaration``, ``record_declaration``,
  ``annotation_type_declaration``. Fields: ``name`` (identifier),
  ``body``, and for classes ``superclass`` (node ``superclass`` wrapping
  the type) + ``interfaces`` (node ``super_interfaces`` wrapping a
  ``type_list``). Base type nodes are ``type_identifier``,
  ``scoped_type_identifier`` (already dotted), or ``generic_type``
  (first named child is the raw type — type_arguments stripped).
- Annotations/modifiers live INSIDE the declaration node (``modifiers``
  child with unnamed keyword tokens like ``static``/``final`` plus
  ``marker_annotation``/``annotation`` named children), so a declaration's
  span starts at its first annotation and its javadoc is simply the
  previous named sibling.
- Members: ``method_declaration`` (fields ``type_parameters``, ``type``
  = return type, ``name``, ``parameters``; ``throws`` is a non-field
  named child), ``constructor_declaration`` (no ``type`` field; body is
  ``constructor_body``), ``compact_constructor_declaration`` (records),
  ``field_declaration``/``constant_declaration`` (one or more
  ``declarator`` -> ``variable_declarator`` with ``name``/``value``
  fields; interface ``constant_declaration`` is implicitly static
  final). Enum bodies hold ``enum_constant`` entries plus an
  ``enum_body_declarations`` wrapper for fields/methods.
- Imports: ``import_declaration`` named children are the path
  (``scoped_identifier``) plus an ``asterisk`` for wildcards — so the
  path text is already exactly what we emit (``java.util`` for
  ``import java.util.*``; full path for static imports, whose ``static``
  keyword is an unnamed token).
- Calls: ``method_invocation`` (fields ``object``/``name``/``arguments``;
  ``object`` may be ``identifier``, ``this``, ``field_access`` with
  ``object``/``field`` fields, or another ``method_invocation`` for
  chained calls — chains through call results are unresolvable and
  skipped, mirroring javascript.py; the inner invocation is still
  visited by the walk) and ``object_creation_expression`` (field
  ``type``: ``type_identifier``/``scoped_type_identifier``/
  ``generic_type``).

Divergence from TypeScript (deliberate): ``implements`` interfaces ARE
included in ``bases``. In Java, interface implementation is the primary
inheritance/polymorphism mechanism (unlike TS where ``implements`` is a
purely type-level relation), so ``super_interfaces`` carries real graph
value alongside ``superclass``.

Other conventions:
- ``package_declaration`` is transparent: no unit, qnames stay
  path-based per design decision D-16.
- static final UPPER_CASE fields -> CONSTANT; enum constants are
  skipped (the enum CLASS unit covers them).
- references collect both ``identifier`` and ``type_identifier`` nodes
  in bodies (Java spells type usage with a distinct node type).
- docstrings are ``/** ... */`` javadoc directly above a declaration;
  the module docstring is a leading block comment NOT attached to the
  first type declaration (a header above ``package``/``import`` is a
  file comment, hence the module's).
"""

from __future__ import annotations

from tree_sitter import Node

from core.parsing.languages._shared import (
    _clean_block_comment,
    _is_constant_name,
    _make_unit,
    _ParseInputs,
    _slice_source,
    _text,
)
from schemas import IngestionUnit, UnitKind

_TYPE_DECL_TYPES = frozenset({
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
})

_METHOD_DECL_TYPES = frozenset({
    "method_declaration",
    "constructor_declaration",
    "compact_constructor_declaration",
})

# Field-ish members whose UPPER_CASE static-final declarators become
# CONSTANT units. Interface constant_declaration is implicitly static
# final, so it skips the modifier check.
_FIELD_DECL_TYPES = frozenset({
    "field_declaration",
    "constant_declaration",
})


def _javadoc_for(node: Node) -> str | None:
    """Javadoc (`/** ... */`) block comment directly above a declaration."""
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "block_comment":
        text = _text(prev)
        if text.startswith("/**"):
            return _clean_block_comment(text)
    return None


def module_docstring(root: Node) -> str | None:
    children = root.named_children
    first = children[0] if children else None
    if first is None or first.type != "block_comment":
        return None
    text = _text(first)
    if not text.startswith("/*"):
        return None
    nxt = first.next_named_sibling
    if text.startswith("/**") and nxt is not None and nxt.type in _TYPE_DECL_TYPES:
        # Javadoc immediately above a type declaration belongs to it.
        return None
    return _clean_block_comment(text)


def extract_children(root: Node, inputs: _ParseInputs) -> list[IngestionUnit]:
    out: list[IngestionUnit] = []
    for node in root.named_children:
        # package_declaration is transparent (D-16); imports handled in
        # extract_imports.
        if node.type in _TYPE_DECL_TYPES:
            out.extend(_emit_type(node, inputs, inputs.module_qname))
    return out


# ---------------------------------------------------------------------------
# Type declarations
# ---------------------------------------------------------------------------
def _type_name(node: Node | None) -> str | None:
    """Raw dotted type name with type arguments stripped."""
    if node is None:
        return None
    if node.type == "generic_type":
        inner = node.named_children[0] if node.named_children else None
        return _type_name(inner)
    if node.type in ("type_identifier", "scoped_type_identifier"):
        return _text(node)
    return None


def _bases(decl: Node) -> list[str]:
    """superclass + super_interfaces type names (see module docstring)."""
    bases: list[str] = []
    sup = decl.child_by_field_name("superclass")
    if sup is not None:  # `superclass` node wraps the extended type
        for child in sup.named_children:
            name = _type_name(child)
            if name:
                bases.append(name)
    ifaces = decl.child_by_field_name("interfaces")
    if ifaces is not None:  # `super_interfaces` node wraps a type_list
        for child in ifaces.named_children:
            if child.type != "type_list":
                continue
            for t in child.named_children:
                name = _type_name(t)
                if name:
                    bases.append(name)
    return bases


def _body_members(body: Node) -> list[Node]:
    """Direct members of a type body, unwrapping enum_body_declarations."""
    members: list[Node] = []
    for member in body.named_children:
        if member.type == "enum_body_declarations":
            members.extend(member.named_children)
        else:
            members.append(member)
    return members


def _emit_type(
    decl: Node,
    inputs: _ParseInputs,
    parent_qname: str,
) -> list[IngestionUnit]:
    name_node = decl.child_by_field_name("name")
    if name_node is None:
        return []
    name = _text(name_node)
    qname = f"{parent_qname}.{name}" if parent_qname else name

    children: list[IngestionUnit] = []
    body = decl.child_by_field_name("body")
    if body is not None:
        for member in _body_members(body):
            if member.type in _METHOD_DECL_TYPES:
                children.append(_emit_method(member, inputs, qname))
            elif member.type in _FIELD_DECL_TYPES:
                children.extend(_emit_constants(member, inputs, qname))
            elif member.type in _TYPE_DECL_TYPES:
                # Nested types: recurse, flattening onto the qname chain.
                children.extend(_emit_type(member, inputs, qname))
            # enum_constant: skipped — the enum CLASS unit covers them.

    children.sort(key=lambda u: (u.line_start, u.name))

    line_start = decl.start_point.row + 1
    line_end = decl.end_point.row + 1
    type_unit = _make_unit(
        inputs=inputs,
        kind=UnitKind.CLASS,
        name=name,
        qualified_name=qname,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=_javadoc_for(decl),
        signature=None,
        imports=[],
        calls=[],
        references=[],
        bases=_bases(decl),
    )
    return [type_unit, *children]


# ---------------------------------------------------------------------------
# Methods + constructors
# ---------------------------------------------------------------------------
def _java_signature(name: str, node: Node) -> str:
    """`name<T>(params): ReturnType` from grammar fields verbatim.

    Constructors (no `type` field) have no return-type suffix.
    """
    type_params = node.child_by_field_name("type_parameters")
    tp_text = _text(type_params) if type_params is not None else ""
    params = node.child_by_field_name("parameters")
    params_text = _text(params) if params is not None else "()"
    ret = node.child_by_field_name("type")
    ret_text = f": {_text(ret)}" if ret is not None else ""
    return f"{name}{tp_text}{params_text}{ret_text}"


def _emit_method(fn: Node, inputs: _ParseInputs, parent_qname: str) -> IngestionUnit:
    name_node = fn.child_by_field_name("name")
    name = _text(name_node) if name_node is not None else "<anonymous>"
    qname = f"{parent_qname}.{name}" if parent_qname else name

    line_start = fn.start_point.row + 1
    line_end = fn.end_point.row + 1
    calls, references = _walk_body(fn)

    return _make_unit(
        inputs=inputs,
        kind=UnitKind.METHOD,
        name=name,
        qualified_name=qname,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=_javadoc_for(fn),
        signature=_java_signature(name, fn),
        imports=[],
        calls=calls,
        references=references,
        bases=[],
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
def _is_static_final(decl: Node) -> bool:
    if decl.type == "constant_declaration":
        return True  # interface fields are implicitly static final
    mods = next((c for c in decl.named_children if c.type == "modifiers"), None)
    if mods is None:
        return False
    kinds = {c.type for c in mods.children}
    return "static" in kinds and "final" in kinds


def _emit_constants(
    decl: Node,
    inputs: _ParseInputs,
    parent_qname: str,
) -> list[IngestionUnit]:
    if not _is_static_final(decl):
        return []
    out: list[IngestionUnit] = []
    line_start = decl.start_point.row + 1
    line_end = decl.end_point.row + 1
    for declarator in decl.children_by_field_name("declarator"):
        name_node = declarator.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node)
        if not _is_constant_name(name):
            continue
        out.append(
            _make_unit(
                inputs=inputs,
                kind=UnitKind.CONSTANT,
                name=name,
                qualified_name=f"{parent_qname}.{name}",
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


# ---------------------------------------------------------------------------
# Calls + references
# ---------------------------------------------------------------------------
def _receiver_chain(node: Node | None) -> str | None:
    """Dotted receiver from field_access/identifier/this chains.

    Returns None for unresolvable shapes (call results, array access,
    parenthesized expressions) — skipped rather than emitted as noise,
    mirroring javascript._member_chain.
    """
    parts: list[str] = []
    cur: Node | None = node
    while cur is not None and cur.type == "field_access":
        fld = cur.child_by_field_name("field")
        if fld is None:
            return None
        parts.append(_text(fld))
        cur = cur.child_by_field_name("object")
    if cur is not None and cur.type in ("identifier", "this"):
        parts.append(_text(cur))
        return ".".join(reversed(parts))
    return None


def _invocation_target(node: Node) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    name = _text(name_node)
    obj = node.child_by_field_name("object")
    if obj is None:
        return name  # plain call: `helper()`
    receiver = _receiver_chain(obj)
    if receiver is None:
        return None  # chained through a call result etc. — unresolvable
    return f"{receiver}.{name}"


def _creation_target(node: Node) -> str | None:
    return _type_name(node.child_by_field_name("type"))


def _walk_body(fn: Node) -> tuple[list[str], list[str]]:
    """Collect call targets + identifier references in a method subtree.

    Anonymous classes/lambdas inside the body are NOT separate units, so
    their calls attribute to the enclosing method (parity with the JS
    extractor's closure handling).
    """
    calls: list[str] = []
    references: list[str] = []
    body = fn.child_by_field_name("body")
    if body is None:
        return calls, references
    stack: list[Node] = [body]
    while stack:
        node = stack.pop()
        if node.type == "method_invocation":
            target = _invocation_target(node)
            if target:
                calls.append(target)
        elif node.type == "object_creation_expression":
            target = _creation_target(node)
            if target:
                calls.append(target)
        elif node.type in ("identifier", "type_identifier"):
            references.append(_text(node))
        stack.extend(node.named_children)
    return calls, references


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
def extract_imports(root: Node, inputs: _ParseInputs) -> list[str]:
    out: list[str] = []
    for node in root.named_children:
        if node.type != "import_declaration":
            continue
        # The path child is the scoped_identifier (or bare identifier);
        # for wildcards the grammar already splits off the `asterisk`,
        # so the path text is exactly the module ("java.util" for
        # `import java.util.*`). Static imports keep their full path.
        path = next(
            (
                c
                for c in node.named_children
                if c.type in ("scoped_identifier", "identifier")
            ),
            None,
        )
        if path is None:
            continue
        dotted = _text(path)
        if dotted:
            out.append(dotted)
    return out
