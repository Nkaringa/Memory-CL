from __future__ import annotations

import ast
import time
from dataclasses import dataclass

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

_tracer = get_tracer("core.parsing.python_parser")


# ---------------------------------------------------------------------------
# Helpers — AST → string conversions
# ---------------------------------------------------------------------------
def _attr_chain(node: ast.AST) -> str | None:
    """Reconstruct a dotted name from an Attribute/Name chain.

    Returns None for chains that don't bottom out in a Name (e.g. method
    calls on subscriptions, calls, lambdas — those are unresolvable
    statically and we skip them rather than emit noise.)
    """
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Reconstruct a compact function/method signature for retrieval.

    `ast.unparse` would include the full body and decorators; we want
    only the def-line, so we re-emit args manually.
    """
    args = fn.args
    pieces: list[str] = []

    # positional-only
    for a in args.posonlyargs:
        pieces.append(_arg_str(a))
    if args.posonlyargs:
        pieces.append("/")

    # regular positional
    for a in args.args:
        pieces.append(_arg_str(a))

    # *args
    if args.vararg:
        pieces.append("*" + _arg_str(args.vararg))
    elif args.kwonlyargs:
        pieces.append("*")

    # keyword-only
    for a in args.kwonlyargs:
        pieces.append(_arg_str(a))

    # **kwargs
    if args.kwarg:
        pieces.append("**" + _arg_str(args.kwarg))

    sig = f"{fn.name}({', '.join(pieces)})"
    if fn.returns is not None:
        sig += f" -> {ast.unparse(fn.returns)}"
    prefix = "async def " if isinstance(fn, ast.AsyncFunctionDef) else "def "
    return prefix + sig


def _arg_str(a: ast.arg) -> str:
    return f"{a.arg}: {ast.unparse(a.annotation)}" if a.annotation else a.arg


def _slice_source(source: str, line_start: int, line_end: int) -> str:
    """Return raw source for [line_start, line_end] inclusive (1-indexed)."""
    lines = source.splitlines(keepends=True)
    return "".join(lines[line_start - 1 : line_end])


def _is_constant_assign(node: ast.stmt) -> ast.Assign | None:
    """Return the Assign iff `node` is a top-level UPPER_CASE constant."""
    if not isinstance(node, ast.Assign):
        return None
    if len(node.targets) != 1:
        return None
    target = node.targets[0]
    if isinstance(target, ast.Name) and target.id.isupper() and target.id.replace("_", "").isalnum():
        return node
    return None


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


class PythonParser:
    """Convert Python source into a deterministic list of `IngestionUnit`s.

    Determinism: child symbols are sorted by `(line_start, name)` before
    emission (PHASE_2_PLAN §6 rule 2). Module is always emitted first so
    downstream consumers can rely on the ordering.
    """

    def parse_file(
        self,
        *,
        source: str,
        repo_id: str,
        file_path: str,
        commit_sha: str,
    ) -> list[IngestionUnit]:
        start = time.perf_counter()
        with _tracer.start_as_current_span("python_parser.parse_file") as span:
            span.set_attribute("repo_id", repo_id)
            span.set_attribute("file_path", file_path)

            module_qname = module_qname_from_path(file_path)
            try:
                tree = ast.parse(source, filename=file_path)
            except SyntaxError as exc:
                emit_phase2_event(
                    event="parse_failed",
                    operation="python_parser.parse_file",
                    status="failed",
                    duration_ms=(time.perf_counter() - start) * 1000,
                    file_path=file_path,
                    error=str(exc),
                    level="error",
                )
                raise

            inputs = _ParseInputs(
                source=source,
                repo_id=repo_id,
                file_path=file_path,
                commit_sha=commit_sha,
                module_qname=module_qname,
            )
            module_imports = _extract_module_imports(tree)
            children = _extract_children(tree, inputs, parent_qname=module_qname)

            # Module unit — content is the full file source.
            module_unit = _make_unit(
                inputs=inputs,
                kind=UnitKind.MODULE,
                name=module_qname.split(".")[-1] if module_qname else file_path,
                qualified_name=module_qname,
                parent_qualified_name=None,
                line_start=1,
                line_end=max(1, source.count("\n") + 1),
                content=source,
                docstring=ast.get_docstring(tree),
                signature=None,
                imports=module_imports,
                calls=[],
                references=[],
                bases=[],
            )

            children.sort(key=lambda u: (u.line_start, u.name))
            units = [module_unit, *children]

            duration = (time.perf_counter() - start) * 1000
            emit_phase2_event(
                event="parse_ok",
                operation="python_parser.parse_file",
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
# Extractors (module-level functions kept side-effect-free for testability)
# ---------------------------------------------------------------------------
def _extract_module_imports(tree: ast.Module) -> list[str]:
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level > 0:
                # Relative import — record dots so the resolver can re-anchor.
                mod = ("." * node.level) + mod
            for alias in node.names:
                if alias.name == "*":
                    out.append(mod)
                else:
                    out.append(f"{mod}.{alias.name}" if mod else alias.name)
    return out


def _extract_children(
    tree: ast.Module,
    inputs: _ParseInputs,
    *,
    parent_qname: str,
) -> list[IngestionUnit]:
    """Walk top-level statements; emit class/function/constant units."""
    out: list[IngestionUnit] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out.extend(_emit_class(node, inputs, parent_qname))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(_emit_function(node, inputs, parent_qname, kind=UnitKind.FUNCTION))
        elif (assign := _is_constant_assign(node)) is not None:
            out.append(_emit_constant(assign, inputs, parent_qname))
    return out


def _emit_class(
    cls: ast.ClassDef,
    inputs: _ParseInputs,
    parent_qname: str,
) -> list[IngestionUnit]:
    qname = f"{parent_qname}.{cls.name}" if parent_qname else cls.name
    bases = [b for b in (_attr_chain(b) or ast.unparse(b) for b in cls.bases) if b]

    # Class body — methods + class-level constants.
    children: list[IngestionUnit] = []
    for child in cls.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            children.append(_emit_function(child, inputs, qname, kind=UnitKind.METHOD))
        elif (assign := _is_constant_assign(child)) is not None:
            children.append(_emit_constant(assign, inputs, qname))

    children.sort(key=lambda u: (u.line_start, u.name))

    line_start = cls.lineno
    line_end = cls.end_lineno or line_start
    cls_unit = _make_unit(
        inputs=inputs,
        kind=UnitKind.CLASS,
        name=cls.name,
        qualified_name=qname,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=ast.get_docstring(cls),
        signature=None,
        imports=[],
        calls=[],
        references=[],
        bases=bases,
    )
    return [cls_unit, *children]


def _emit_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    inputs: _ParseInputs,
    parent_qname: str,
    *,
    kind: UnitKind,
) -> IngestionUnit:
    qname = f"{parent_qname}.{fn.name}" if parent_qname else fn.name
    line_start = fn.lineno
    line_end = fn.end_lineno or line_start

    calls: list[str] = []
    references: list[str] = []
    for inner in ast.walk(fn):
        if isinstance(inner, ast.Call):
            target = _attr_chain(inner.func)
            if target:
                calls.append(target)
        elif isinstance(inner, ast.Name):
            references.append(inner.id)

    return _make_unit(
        inputs=inputs,
        kind=kind,
        name=fn.name,
        qualified_name=qname,
        parent_qualified_name=parent_qname or None,
        line_start=line_start,
        line_end=line_end,
        content=_slice_source(inputs.source, line_start, line_end),
        docstring=ast.get_docstring(fn),
        signature=_signature(fn),
        imports=[],
        calls=calls,
        references=references,
        bases=[],
    )


def _emit_constant(
    assign: ast.Assign,
    inputs: _ParseInputs,
    parent_qname: str,
) -> IngestionUnit:
    target = assign.targets[0]
    assert isinstance(target, ast.Name)
    qname = f"{parent_qname}.{target.id}" if parent_qname else target.id
    line_start = assign.lineno
    line_end = assign.end_lineno or line_start
    return _make_unit(
        inputs=inputs,
        kind=UnitKind.CONSTANT,
        name=target.id,
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
        language=Language.PYTHON,
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
