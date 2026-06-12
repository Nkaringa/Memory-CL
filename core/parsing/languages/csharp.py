"""C# extraction rules (multilang batch-2, Task 2).

Verified grammar facts (tree-sitter-c-sharp 0.23.5, probed empirically —
throwaway scripts against every construct below plus real Unity sources
from a MonoBehaviour-heavy game project):

- Root node: ``compilation_unit``.
- ``using_directive``: plain ``using System;`` -> single ``identifier``
  child (no field); ``using A.B;`` -> ``qualified_name`` child (fields
  ``qualifier``/``name``, recursive; ``name`` may be ``generic_name``);
  ``using static System.Math;`` -> ``qualified_name`` child, no ``name``
  field; alias ``using F = A.B<int>;`` -> field ``name`` is the alias
  identifier (textually first), the *target* is the remaining named child.
- ``namespace_declaration``: fields ``name`` (identifier|qualified_name),
  ``body`` (declaration_list). ``file_scoped_namespace_declaration``
  (``namespace X;``) has a ``name`` field but NO body — declarations
  that follow it are SIBLINGS at compilation_unit level.
- Type declarations: ``class_declaration`` / ``struct_declaration`` /
  ``interface_declaration`` / ``record_declaration`` /
  ``enum_declaration``, all with field ``name`` (identifier) and field
  ``body`` (``declaration_list``; enums: ``enum_member_declaration_list``).
  ``record struct`` is still ``record_declaration``; positional records
  (``record Person(string Name);``) have a ``parameter_list`` and no body.
  ``attribute_list`` ([Serializable], [SerializeField]) is a *child of
  the declaration itself*, so ``///`` comments stay prev-siblings of the
  declaration node even when attributes are present.
- ``base_list`` is an unnamed-field child of the type declaration; its
  named children are ``identifier`` | ``qualified_name`` |
  ``generic_name`` | ``predefined_type`` (``enum E : byte``) |
  ``primary_constructor_base_type`` (field ``type``; records with base
  constructor arguments, ``record Student(...) : Person(Name)``).
- ``method_declaration``: fields ``returns``, ``name``,
  ``type_parameters`` (optional), ``parameters``, ``body`` (``block`` or
  ``arrow_expression_clause`` for ``=> expr`` bodies). ``async`` is a
  ``modifier`` child whose *text* is "async" — NOT a node of type
  "async", so the shared ``_is_async``/``_signature`` helpers do not fit
  and this module builds its own signature.
- ``constructor_declaration``: fields ``name``, ``parameters``, ``body``.
- ``property_declaration``: fields ``type``, ``name``, then either
  ``accessors`` (``accessor_list`` of ``accessor_declaration``; the
  accessor has a ``body`` field — block or arrow_expression_clause —
  only when non-auto) and/or ``value``. ``value`` is an
  ``arrow_expression_clause`` for ``int Hp => 42;`` (a real body) but a
  plain initializer expression for auto-property ``= "x"`` (NOT a body).
- ``field_declaration``: ``modifier`` children (text "const"/"static"/
  "readonly"/...), then ``variable_declaration`` ->
  ``variable_declarator`` (field ``name``).
- ``local_function_statement``: fields ``type``, ``name``,
  ``parameters``, ``body``. NOT emitted as units (Python parity — a
  ``def`` nested in a function body is no unit either): their bodies are
  walked as part of the enclosing method, so their calls/references
  attribute to it. Emitting them would also produce the EDGE_RULES-
  forbidden Method-DEFINES->Function structural edge.
- ``invocation_expression``: fields ``function``, ``arguments``. The
  function is ``identifier`` | ``generic_name`` (``GetComponent<T>()``,
  reconstructed as just "GetComponent") | ``member_access_expression``
  (fields ``expression``/``name``; the chain bottoms out at
  ``identifier``, ``this``, ``base``, or ``predefined_type`` —
  ``string.Join``) | ``conditional_access_expression`` (``obj?.Fire()``,
  unresolvable -> skipped).
- ``object_creation_expression``: fields ``type`` (identifier |
  qualified_name | generic_name | predefined_type), ``arguments``.
- ``///`` doc comments are one ``comment`` node *per line*, emitted as
  prev-siblings of the declaration; attachment requires line-contiguity.
"""

from __future__ import annotations

import re

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
    "struct_declaration",
    "interface_declaration",
    "record_declaration",
    "enum_declaration",
})

_XML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")


# ---------------------------------------------------------------------------
# Name reconstruction
# ---------------------------------------------------------------------------
def _dotted(node: Node | None) -> str | None:
    """Dotted name from C# name/member-access shapes; None = unresolvable.

    Mirrors _shared._member_chain's policy: call results, conditional
    access (`?.`), and other dynamic shapes return None — skipped rather
    than emitted as noise. Generic arity is dropped (`List<int>` -> "List").
    """
    if node is None:
        return None
    t = node.type
    if t in ("identifier", "predefined_type", "this", "base"):
        return _text(node)
    if t == "generic_name":
        for child in node.named_children:
            if child.type == "identifier":
                return _text(child)
        return None
    if t in ("qualified_name", "member_access_expression"):
        left_field = "qualifier" if t == "qualified_name" else "expression"
        left = _dotted(node.child_by_field_name(left_field))
        right = _dotted(node.child_by_field_name("name"))
        if left and right:
            return f"{left}.{right}"
        return None
    if t == "alias_qualified_name":  # global::X.Y
        return _dotted(node.child_by_field_name("name"))
    if t == "primary_constructor_base_type":
        return _dotted(node.child_by_field_name("type"))
    return None


# ---------------------------------------------------------------------------
# Doc comments
# ---------------------------------------------------------------------------
def _strip_doc_line(raw: str) -> str:
    """`/// <summary>Moves.</summary>` -> "Moves." (XML decoration removed)."""
    body = raw[3:] if raw.startswith("///") else raw.lstrip("/")
    return _XML_TAG_RE.sub("", body).strip()


def _doc_comment_for(node: Node) -> str | None:
    """Contiguous `///` run directly above a declaration, cleaned."""
    parts: list[str] = []
    expected_row = node.start_point.row  # comment must end on the line above
    prev = node.prev_named_sibling
    while prev is not None and prev.type == "comment":
        text = _text(prev)
        if not text.startswith("///") or prev.end_point.row != expected_row - 1:
            break
        parts.append(text)
        expected_row = prev.start_point.row
        prev = prev.prev_named_sibling
    if not parts:
        return None
    lines = [_strip_doc_line(raw) for raw in reversed(parts)]
    return "\n".join(ln for ln in lines if ln) or None


def module_docstring(root: Node) -> str | None:
    """Leading // or /* run at the top of the file, unless it is a `///`
    doc run attached to the first declaration (mirrors javascript.py's
    rule shape: doc-form comments above a declaration belong to it)."""
    children = root.named_children
    first = children[0] if children else None
    if first is None or first.type != "comment":
        return None
    text = _text(first)
    if text.startswith("/*"):
        return _clean_block_comment(text)
    # Contiguous run of line comments from the top of the file.
    run = [first]
    while True:
        nxt = run[-1].next_named_sibling
        if (
            nxt is None
            or nxt.type != "comment"
            or not _text(nxt).startswith("//")
            or nxt.start_point.row != run[-1].end_point.row + 1
        ):
            break
        run.append(nxt)
    follower = run[-1].next_named_sibling
    if (
        text.startswith("///")
        and follower is not None
        and follower.start_point.row == run[-1].end_point.row + 1
    ):
        return None  # `///` doc run belongs to the first declaration
    lines = [_strip_doc_line(_text(c)) for c in run]
    return "\n".join(ln for ln in lines if ln) or None


# ---------------------------------------------------------------------------
# Children
# ---------------------------------------------------------------------------
def extract_children(root: Node, inputs: _ParseInputs) -> list[IngestionUnit]:
    out: list[IngestionUnit] = []
    _walk_declarations(root.named_children, inputs, inputs.module_qname, out)
    return out


def _walk_declarations(
    nodes: list[Node],
    inputs: _ParseInputs,
    parent_qname: str,
    out: list[IngestionUnit],
) -> None:
    for node in nodes:
        if node.type == "namespace_declaration":
            # Namespaces are transparent (design D-16: path-based qnames;
            # the namespace itself emits no unit).
            body = node.child_by_field_name("body")
            if body is not None:
                _walk_declarations(body.named_children, inputs, parent_qname, out)
        elif node.type in _TYPE_DECL_TYPES:
            out.extend(_emit_type(node, inputs, parent_qname))
        # file_scoped_namespace_declaration has no body — the declarations
        # that follow it are siblings, handled by this same loop.


def _emit_type(
    decl: Node, inputs: _ParseInputs, parent_qname: str
) -> list[IngestionUnit]:
    name_node = decl.child_by_field_name("name")
    if name_node is None:
        return []
    name = _text(name_node)
    qname = f"{parent_qname}.{name}" if parent_qname else name

    bases: list[str] = []
    for child in decl.named_children:
        if child.type != "base_list":
            continue
        for base in child.named_children:
            dotted = _dotted(base)
            if dotted:
                bases.append(dotted)

    members: list[IngestionUnit] = []
    body = decl.child_by_field_name("body")
    if body is not None and body.type == "declaration_list":
        for member in body.named_children:
            if member.type in ("method_declaration", "constructor_declaration"):
                members.extend(
                    _emit_callable(member, inputs, qname, kind=UnitKind.METHOD)
                )
            elif member.type == "property_declaration":
                members.extend(_emit_property(member, inputs, qname))
            elif member.type == "field_declaration":
                members.extend(_emit_fields(member, inputs, qname))
            elif member.type in _TYPE_DECL_TYPES:
                members.extend(_emit_type(member, inputs, qname))

    members.sort(key=lambda u: (u.line_start, u.name))

    line_start = decl.start_point.row + 1
    line_end = decl.end_point.row + 1
    # EDGE_RULES forbids Class-DEFINES->Class, so nested types parent on
    # the MODULE, not the enclosing type. The qualified_name still encodes
    # the nesting (`mod.Outer.Inner`), so no information is lost — the
    # structural edge just stays the legal Module-DEFINES->Class.
    type_unit = _make_unit(
        inputs=inputs,
        kind=UnitKind.CLASS,
        name=name,
        qualified_name=qname,
        parent_qualified_name=inputs.module_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=_doc_comment_for(decl),
        signature=None,
        imports=[],
        calls=[],
        references=[],
        bases=bases,
    )
    return [type_unit, *members]


def _cs_signature(name: str, node: Node) -> str:
    """name + type params + parameter list (+ return type where exposed).

    C#'s `async` is a modifier-with-text and the return type sits in the
    `returns` field, so the shared `_signature` helper does not apply.
    """
    is_async = any(
        c.type == "modifier" and _text(c) == "async" for c in node.children
    )
    prefix = "async " if is_async else ""
    type_params = node.child_by_field_name("type_parameters")
    tp_text = _text(type_params) if type_params is not None else ""
    params = node.child_by_field_name("parameters")
    params_text = _text(params) if params is not None else "()"
    # method_declaration exposes the return type as `returns`
    # (constructors have neither field — no suffix).
    ret = node.child_by_field_name("returns")
    ret_text = f" -> {_text(ret)}" if ret is not None else ""
    return f"{prefix}{name}{tp_text}{params_text}{ret_text}"


def _emit_callable(
    fn: Node,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    kind: UnitKind,
) -> list[IngestionUnit]:
    """A method or constructor declaration."""
    name_node = fn.child_by_field_name("name")
    name = _text(name_node) if name_node is not None else "<anonymous>"
    qname = f"{parent_qname}.{name}" if parent_qname else name

    calls, references = _walk_body(fn.child_by_field_name("body"))

    line_start = fn.start_point.row + 1
    line_end = fn.end_point.row + 1
    unit = _make_unit(
        inputs=inputs,
        kind=kind,
        name=name,
        qualified_name=qname,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=_doc_comment_for(fn),
        signature=_cs_signature(name, fn),
        imports=[],
        calls=calls,
        references=references,
        bases=[],
    )
    return [unit]


def _emit_property(
    member: Node, inputs: _ParseInputs, parent_qname: str
) -> list[IngestionUnit]:
    """Properties with real bodies -> METHOD; auto-properties skipped."""
    name_node = member.child_by_field_name("name")
    if name_node is None:
        return []
    name = _text(name_node)

    bodies: list[Node] = []
    value = member.child_by_field_name("value")
    if value is not None and value.type == "arrow_expression_clause":
        bodies.append(value)  # `int Hp => 42;`
    accessors = member.child_by_field_name("accessors")
    if accessors is not None:
        for accessor in accessors.named_children:
            acc_body = accessor.child_by_field_name("body")
            if acc_body is not None:
                bodies.append(acc_body)  # `get { ... }` / `get => _x;`
    if not bodies:
        return []  # auto-property (`{ get; set; }`, optionally initialized)

    qname = f"{parent_qname}.{name}" if parent_qname else name
    calls: list[str] = []
    references: list[str] = []
    for body in bodies:
        c, r = _walk_subtree(body)
        calls.extend(c)
        references.extend(r)

    prop_type = member.child_by_field_name("type")
    signature = f"{name} -> {_text(prop_type)}" if prop_type is not None else name

    line_start = member.start_point.row + 1
    line_end = member.end_point.row + 1
    unit = _make_unit(
        inputs=inputs,
        kind=UnitKind.METHOD,
        name=name,
        qualified_name=qname,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=_doc_comment_for(member),
        signature=signature,
        imports=[],
        calls=calls,
        references=references,
        bases=[],
    )
    return [unit]


def _emit_fields(
    member: Node, inputs: _ParseInputs, parent_qname: str
) -> list[IngestionUnit]:
    """const fields -> CONSTANT always; static readonly only when UPPER."""
    modifiers = {_text(c) for c in member.children if c.type == "modifier"}
    is_const = "const" in modifiers
    is_static_readonly = "static" in modifiers and "readonly" in modifiers
    if not (is_const or is_static_readonly):
        return []

    var_decl = next(
        (c for c in member.named_children if c.type == "variable_declaration"),
        None,
    )
    if var_decl is None:
        return []

    out: list[IngestionUnit] = []
    line_start = member.start_point.row + 1
    line_end = member.end_point.row + 1
    for declarator in var_decl.named_children:
        if declarator.type != "variable_declarator":
            continue
        name_node = declarator.child_by_field_name("name")
        if name_node is None:
            continue
        name = _text(name_node)
        if not is_const and not _is_constant_name(name):
            continue  # static readonly camelCase — not a constant
        out.append(
            _make_unit(
                inputs=inputs,
                kind=UnitKind.CONSTANT,
                name=name,
                qualified_name=f"{parent_qname}.{name}" if parent_qname else name,
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


def _walk_body(body: Node | None) -> tuple[list[str], list[str]]:
    if body is None:
        return [], []
    return _walk_subtree(body)


def _walk_subtree(body: Node) -> tuple[list[str], list[str]]:
    """Calls + identifier references in a body subtree.

    Local functions are NOT separate units (Python parity; see module
    docstring), so the walk descends into them — their calls/references
    attribute to the enclosing method, same as JS closures.
    """
    calls: list[str] = []
    references: list[str] = []
    stack: list[Node] = [body]
    while stack:
        node = stack.pop()
        if node.type == "invocation_expression":
            target = _dotted(node.child_by_field_name("function"))
            if target:
                calls.append(target)
        elif node.type == "object_creation_expression":
            target = _dotted(node.child_by_field_name("type"))
            if target:
                calls.append(target)
        elif node.type == "identifier":
            references.append(_text(node))
        stack.extend(node.named_children)
    return calls, references


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
def _using_target(directive: Node) -> str | None:
    """`using UnityEngine;` -> "UnityEngine"; `using static X.Y` -> "X.Y";
    `using F = A.B` -> "A.B" (alias name is the textually-first child)."""
    candidates = [
        c
        for c in directive.named_children
        if c.type
        in ("identifier", "qualified_name", "generic_name", "alias_qualified_name")
    ]
    if not candidates:
        return None
    # With an alias, the alias identifier (field `name`) precedes the
    # target; without one the single candidate IS the target.
    return _dotted(candidates[-1])


def extract_imports(root: Node, inputs: _ParseInputs) -> list[str]:
    out: list[str] = []
    _collect_usings(root, out)
    return out


def _collect_usings(scope: Node, out: list[str]) -> None:
    for child in scope.named_children:
        if child.type == "using_directive":
            target = _using_target(child)
            if target:
                out.append(target)
        elif child.type == "namespace_declaration":
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_usings(body, out)
