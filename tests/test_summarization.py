from __future__ import annotations

import textwrap

from core.ingestion import GraphBuilder
from core.parsing import PythonParser
from core.summarization import ApiSummarizer, GraphSummarizer, ModuleSummarizer
from schemas import NodeKind

REPO = "r"
COMMIT = "c"


def _units(source: str, file_path: str = "pkg/m.py") -> list:
    return PythonParser().parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


# ---- ModuleSummarizer ------------------------------------------------------
def test_module_summarizer_groups_per_module() -> None:
    a = _units("""
        import os
        def fa(): pass
        class Ca: pass
        CONST = 1
    """, file_path="a.py")
    b = _units("""
        import json
        def fb(): pass
    """, file_path="b.py")

    mods = ModuleSummarizer().summarize(a + b)
    assert [m.id for m in mods] == ["a", "b"]

    by_id = {m.id: m for m in mods}
    assert by_id["a"].fn == ["fa"]
    assert by_id["a"].cls == ["Ca"]
    assert by_id["a"].const == ["CONST"]
    assert by_id["a"].imp == ["os"]
    assert by_id["a"].file == ["a.py"]

    assert by_id["b"].fn == ["fb"]
    assert by_id["b"].cls == []


def test_module_summarizer_skips_methods_and_class_constants_at_top() -> None:
    """Module's `fn`/`const` arrays should NOT include class-internal symbols."""
    units = _units("""
        VERSION = '1'
        def f(): pass
        class C:
            INNER = 'x'
            def m(self): pass
    """)
    [m] = ModuleSummarizer().summarize(units)
    assert "f" in m.fn
    assert "VERSION" in m.const
    # Class-internal symbols don't pollute module-level lists.
    assert "INNER" not in m.const
    assert "m" not in m.fn


def test_module_summarizer_is_byte_deterministic() -> None:
    src = "def b(): pass\ndef a(): pass\nclass C: pass\n"
    a = ModuleSummarizer().summarize(_units(src))
    b = ModuleSummarizer().summarize(_units(src))
    assert [m.to_dense_json() for m in a] == [m.to_dense_json() for m in b]


# ---- ApiSummarizer ---------------------------------------------------------
def test_api_summarizer_extracts_public_top_level_only() -> None:
    units = _units("""
        def public(): pass
        def _private(): pass
        class PublicCls: pass
        class _PrivateCls: pass
        def __dunder__(): pass
    """)
    [api] = ApiSummarizer().summarize(units)
    assert api.id == "pkg.m"
    assert api.api == ["public"]
    assert api.cls == ["PublicCls"]


def test_api_summarizer_drops_modules_with_empty_api() -> None:
    units = _units("def _hidden(): pass\n")
    apis = ApiSummarizer().summarize(units)
    assert apis == []


def test_api_summarizer_does_not_include_methods() -> None:
    units = _units("""
        class Service:
            def public_method(self): pass
        def standalone(): pass
    """)
    [api] = ApiSummarizer().summarize(units)
    assert api.api == ["standalone"]
    assert "public_method" not in api.api


# ---- GraphSummarizer -------------------------------------------------------
def test_graph_summarizer_skips_external_nodes() -> None:
    units = _units("""
        import os
        def f(): os.path.join('a', 'b')
    """)
    res = GraphBuilder().build(units)
    slices = GraphSummarizer().summarize(res.nodes, res.edges)
    kinds = {s.k for s in slices}
    assert NodeKind.EXTERNAL.value not in kinds


def test_graph_summarizer_records_in_out_and_degree() -> None:
    units = _units("""
        def callee(): pass
        def caller(): callee()
    """)
    res = GraphBuilder().build(units)
    slices = {s.id: s for s in GraphSummarizer().summarize(res.nodes, res.edges)}
    callee_unit = next(u for u in units if u.name == "callee")
    caller_unit = next(u for u in units if u.name == "caller")

    callee_slice = slices[callee_unit.unit_id]
    caller_slice = slices[caller_unit.unit_id]
    assert caller_unit.unit_id in callee_slice.i  # callee is called by caller
    assert callee_unit.unit_id in caller_slice.o  # caller calls callee
    assert callee_slice.deg == len(callee_slice.i) + len(callee_slice.o)


def test_graph_summarizer_is_deterministic() -> None:
    units = _units("def f(): g()\ndef g(): f()\n")
    res = GraphBuilder().build(units)
    a = GraphSummarizer().summarize(res.nodes, res.edges)
    b = GraphSummarizer().summarize(res.nodes, res.edges)
    assert [s.to_dense_json() for s in a] == [s.to_dense_json() for s in b]
