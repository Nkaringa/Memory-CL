"""Language-agnostic helpers shared by the per-language extractor modules.

Moved verbatim from `core.parsing.treesitter_parser` (multilang batch-2
Task 1). Names keep their leading underscore on purpose — they are the
package-private toolkit of `core.parsing.languages.*`, imported
explicitly by each extractor module:

    _ParseInputs          — per-file parse context dataclass
    _text                 — node bytes -> str
    _slice_source         — 1-indexed inclusive line slice of the source
    _clean_block_comment  — strip /** ... */ (or /* ... */) decoration
    _member_chain         — member_expression/identifier chain -> dotted name
    _is_constant_name     — UPPER_CASE rule (same as the Python parser)
    _is_async             — has an `async` child
    _signature            — name + type params + params + return type
    _jsdoc_for            — JSDoc-style block comment directly above a node
    _make_unit            — IngestionUnit factory wired to _ParseInputs
"""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from schemas import (
    IngestionUnit,
    Language,
    UnitKind,
    content_sha,
    stable_unit_id,
)


@dataclass(slots=True)
class _ParseInputs:
    source: str
    repo_id: str
    file_path: str
    commit_sha: str
    module_qname: str
    language: Language


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
    type_params = node.child_by_field_name("type_parameters")
    tp_text = _text(type_params) if type_params is not None else ""
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
    return f"{prefix}{name}{tp_text}{params_text}{ret_text}"


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
