from __future__ import annotations

import textwrap

from core.ingestion import GraphBuilder
from core.parsing import PythonParser
from schemas import EdgeKind, NodeKind

REPO = "r"
COMMIT = "c"


def _units(source: str, file_path: str = "pkg/mod.py") -> list:
    return PythonParser().parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


def test_builder_emits_file_node_and_unit_nodes() -> None:
    units = _units("""
        def f(): pass
        class C:
            def m(self): pass
    """)
    res = GraphBuilder().build(units)
    by_kind: dict[NodeKind, list] = {}
    for n in res.nodes:
        by_kind.setdefault(n.kind, []).append(n)
    assert NodeKind.FILE in by_kind and len(by_kind[NodeKind.FILE]) == 1
    assert NodeKind.MODULE in by_kind and len(by_kind[NodeKind.MODULE]) == 1
    assert NodeKind.FUNCTION in by_kind and len(by_kind[NodeKind.FUNCTION]) == 1
    assert NodeKind.CLASS in by_kind and len(by_kind[NodeKind.CLASS]) == 1
    assert NodeKind.METHOD in by_kind and len(by_kind[NodeKind.METHOD]) == 1


def test_structural_edges_are_correct() -> None:
    units = _units("""
        def f(): pass
        class C:
            def m(self): pass
    """)
    res = GraphBuilder().build(units)
    edges_by_kind: dict[str, list] = {}
    for e in res.edges:
        edges_by_kind.setdefault(e.kind.value, []).append(e)

    # File CONTAINS each non-module unit (function, class, method).
    assert len(edges_by_kind["CONTAINS"]) == 3
    # Module DEFINES function + class; class DEFINES method.
    defines = edges_by_kind["DEFINES"]
    src_kinds = {next(n.kind for n in res.nodes if n.node_id == e.src_id)
                 for e in defines}
    assert {NodeKind.MODULE, NodeKind.CLASS}.issubset(src_kinds)


def test_imports_resolve_to_external_when_unknown() -> None:
    units = _units("""
        import os
        from typing import List
    """)
    res = GraphBuilder().build(units)
    imports = [e for e in res.edges if e.kind == EdgeKind.IMPORTS]
    target_kinds = {next(n.kind for n in res.nodes if n.node_id == e.dst_id)
                    for e in imports}
    assert target_kinds == {NodeKind.EXTERNAL}


def test_calls_resolve_in_same_batch() -> None:
    units = _units("""
        def helper():
            return 1

        def caller():
            return helper()
    """)
    res = GraphBuilder().build(units)
    calls = [e for e in res.edges if e.kind == EdgeKind.CALLS]
    # Should resolve to the in-batch helper, not External.
    assert len(calls) >= 1
    helper_unit = next(u for u in units if u.name == "helper")
    targets = {e.dst_id for e in calls}
    assert helper_unit.unit_id in targets


def test_inherits_resolves_to_external_when_unknown() -> None:
    units = _units("""
        from abc import ABC
        class Foo(ABC):
            pass
    """)
    res = GraphBuilder().build(units)
    inherits = [e for e in res.edges if e.kind == EdgeKind.INHERITS]
    assert len(inherits) == 1
    target = next(n for n in res.nodes if n.node_id == inherits[0].dst_id)
    assert target.kind == NodeKind.EXTERNAL
    assert target.qualified_name == "ABC"


def test_no_self_edges_in_recursive_call() -> None:
    units = _units("""
        def fact(n):
            if n <= 1: return 1
            return n * fact(n - 1)
    """)
    res = GraphBuilder().build(units)
    fact_unit = next(u for u in units if u.name == "fact")
    self_edges = [e for e in res.edges if e.src_id == e.dst_id]
    assert self_edges == []
    # The recursive call resolves to the function itself but is filtered.
    calls_from_fact = [e for e in res.edges
                       if e.src_id == fact_unit.unit_id and e.kind == EdgeKind.CALLS]
    # No CALL edge to itself emitted.
    assert all(e.dst_id != fact_unit.unit_id for e in calls_from_fact)


def test_builder_output_is_byte_deterministic() -> None:
    src = """
        def b(): a()
        def a(): pass
    """
    u1 = _units(src)
    u2 = _units(src)
    r1 = GraphBuilder().build(u1)
    r2 = GraphBuilder().build(u2)
    assert [n.node_id for n in r1.nodes] == [n.node_id for n in r2.nodes]
    assert [(e.kind, e.src_id, e.dst_id) for e in r1.edges] == \
           [(e.kind, e.src_id, e.dst_id) for e in r2.edges]


def test_cross_file_resolution_via_qname_resolver() -> None:
    units_a = _units("def f(): pass\n", file_path="a.py")
    units_b = _units("def caller(): f()\n", file_path="b.py")
    f_unit = next(u for u in units_a if u.name == "f")

    # Caller-side: pretend `f` was imported into b.py so the bare name
    # resolves to the cross-file unit.
    res = GraphBuilder().build(
        units_b,
        qname_resolver={"f": (f_unit.unit_id, NodeKind.FUNCTION)},
    )
    calls = [e for e in res.edges if e.kind == EdgeKind.CALLS]
    assert any(e.dst_id == f_unit.unit_id for e in calls)


def test_edges_validated_against_edge_rules() -> None:
    """Smoke-test: every emitted edge passes is_edge_allowed."""
    from schemas import is_edge_allowed
    units = _units("""
        import os
        class C:
            def m(self): os.path.join('a','b')
        def f(): C()
    """)
    res = GraphBuilder().build(units)
    nodes = {n.node_id: n for n in res.nodes}
    for e in res.edges:
        assert is_edge_allowed(nodes[e.src_id].kind, e.kind, nodes[e.dst_id].kind)


def test_js_units_produce_import_call_and_external_edges() -> None:
    from core.parsing import TreeSitterParser
    from schemas import Language

    source = (
        'import { score } from "./scorer";\n'
        'import React from "react";\n'
        "function helper(x) { return x; }\n"
        "export const run = (x) => helper(score(x));\n"
    )
    units = TreeSitterParser(Language.JAVASCRIPT).parse_file(
        source=source,
        repo_id="r1",
        file_path="web/app.js",
        commit_sha="c1",
    )
    result = GraphBuilder().build(units)

    by_kind = {}
    for e in result.edges:
        by_kind.setdefault(e.kind, []).append(e)

    node_by_id = {n.node_id: n for n in result.nodes}

    # Same-file call helper() resolved to the real unit via the
    # `<module>.callee` candidate — this exercises the _module_qname fix.
    helper_unit = next(u for u in units if u.qualified_name == "web.app.helper")
    run_unit = next(u for u in units if u.qualified_name == "web.app.run")
    call_targets = {
        e.dst_id for e in by_kind.get(EdgeKind.CALLS, []) if e.src_id == run_unit.unit_id
    }
    assert helper_unit.unit_id in call_targets

    # Bare package import → External node.
    external_qnames = {
        n.qualified_name for n in result.nodes if n.kind == NodeKind.EXTERNAL
    }
    assert "react" in external_qnames

    # Module unit carries IMPORTS edges.
    module_unit = units[0]
    import_dsts = {
        node_by_id[e.dst_id].qualified_name
        for e in by_kind.get(EdgeKind.IMPORTS, [])
        if e.src_id == module_unit.unit_id
    }
    assert "react" in import_dsts
    assert "web.scorer.score" in import_dsts
