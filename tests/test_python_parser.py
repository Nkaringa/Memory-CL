from __future__ import annotations

import textwrap

import pytest

from core.parsing import PythonParser, module_qname_from_path
from schemas import UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"


def _parse(source: str, file_path: str = "pkg/mod.py") -> list:
    return PythonParser().parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


def test_module_qname_from_path() -> None:
    assert module_qname_from_path("pkg/mod.py") == "pkg.mod"
    assert module_qname_from_path("pkg/__init__.py") == "pkg"
    assert module_qname_from_path("top.py") == "top"
    assert module_qname_from_path("a/b/c/__init__.py") == "a.b.c"


def test_first_unit_is_module_then_children_sorted_by_line() -> None:
    units = _parse("""
        '''Module docstring.'''
        import os
        from typing import List

        CONSTANT_A = 1

        def alpha():
            return 1

        class Beta:
            def m(self): pass
    """)
    assert units[0].kind == UnitKind.MODULE
    rest = units[1:]
    starts = [u.line_start for u in rest]
    assert starts == sorted(starts)


def test_extracts_imports_into_module_unit() -> None:
    units = _parse("""
        import os
        from typing import List, Tuple
        from . import sibling
    """)
    module = units[0]
    # Validator sorts + dedupes; relative imports retain dot prefix.
    assert module.imports == sorted({
        "os", "typing.List", "typing.Tuple", "..sibling",
    })


def test_classes_and_methods_have_correct_parent_chain() -> None:
    units = _parse("""
        class Service:
            VERSION = '1'
            def __init__(self):
                pass
            async def handle(self, req):
                return req
    """)
    by_qname = {u.qualified_name: u for u in units}
    assert "pkg.mod.Service" in by_qname
    assert by_qname["pkg.mod.Service"].kind == UnitKind.CLASS

    # Methods reference the class as parent.
    init = by_qname["pkg.mod.Service.__init__"]
    handle = by_qname["pkg.mod.Service.handle"]
    assert init.kind == UnitKind.METHOD
    assert handle.kind == UnitKind.METHOD
    assert init.parent_qualified_name == "pkg.mod.Service"
    assert handle.parent_qualified_name == "pkg.mod.Service"

    # Class-level constant lives under the class.
    version = by_qname["pkg.mod.Service.VERSION"]
    assert version.kind == UnitKind.CONSTANT
    assert version.parent_qualified_name == "pkg.mod.Service"


def test_module_level_constants_emitted() -> None:
    units = _parse("""
        VERSION = "1"
        not_a_constant = 2
        __version__ = "x"
    """)
    qnames = {u.qualified_name for u in units}
    assert "pkg.mod.VERSION" in qnames
    # lowercase identifiers are not constants
    assert "pkg.mod.not_a_constant" not in qnames
    # dunder-version is uppercase-insensitive — by our rule it's NOT a constant
    assert "pkg.mod.__version__" not in qnames


def test_annotated_module_constants_emitted() -> None:
    units = _parse("""
        LEGAL_TRANSITIONS: dict[str, list[str]] = {
            "draft": ["active"],
        }
        TIMEOUT_S: int = 30
        lower_annotated: int = 1
        DECLARED_ONLY: int
    """)
    by_qname = {u.qualified_name: u for u in units}

    const = by_qname["pkg.mod.LEGAL_TRANSITIONS"]
    assert const.kind == UnitKind.CONSTANT
    assert const.parent_qualified_name == "pkg.mod"
    # Content slice spans the FULL annotated assignment (multi-line value).
    assert const.line_start == 1
    assert const.line_end == 3
    assert const.content.startswith("LEGAL_TRANSITIONS: dict[str, list[str]] = {")
    assert const.content.rstrip().endswith("}")

    assert by_qname["pkg.mod.TIMEOUT_S"].kind == UnitKind.CONSTANT

    # lowercase annotated assignments are not constants
    assert "pkg.mod.lower_annotated" not in by_qname
    # annotation-only declarations (no value) are not constants
    assert "pkg.mod.DECLARED_ONLY" not in by_qname


def test_annotated_class_constants_emitted() -> None:
    units = _parse("""
        class Service:
            VERSION: str = "1"
            count: int = 0
            PENDING: int

            def m(self):
                pass
    """)
    by_qname = {u.qualified_name: u for u in units}

    version = by_qname["pkg.mod.Service.VERSION"]
    assert version.kind == UnitKind.CONSTANT
    assert version.parent_qualified_name == "pkg.mod.Service"
    assert version.line_start == 2
    assert version.line_end == 2
    assert version.content.strip() == 'VERSION: str = "1"'

    assert "pkg.mod.Service.count" not in by_qname
    assert "pkg.mod.Service.PENDING" not in by_qname


def test_function_signature_is_compact_def_line() -> None:
    units = _parse("""
        def add(a: int, b: int = 0, *args, **kw) -> int:
            '''Adds.'''
            return a + b
    """)
    fn = next(u for u in units if u.name == "add")
    assert fn.signature is not None
    assert fn.signature.startswith("def add(")
    assert "-> int" in fn.signature
    assert "return a + b" not in fn.signature


def test_calls_extracted_for_functions() -> None:
    units = _parse("""
        import os

        def f():
            os.path.join('a', 'b')
            print('x')
            self_call = bar()
            return self_call
    """)
    fn = next(u for u in units if u.name == "f")
    # Sorted+deduped by validator.
    assert "os.path.join" in fn.calls
    assert "print" in fn.calls
    assert "bar" in fn.calls
    assert fn.calls == sorted(set(fn.calls))


def test_class_bases_captured() -> None:
    units = _parse("""
        from abc import ABC
        import typing

        class Foo(ABC, typing.Generic):
            pass
    """)
    cls = next(u for u in units if u.name == "Foo")
    assert "ABC" in cls.bases
    assert "typing.Generic" in cls.bases


def test_parser_is_byte_deterministic() -> None:
    src = textwrap.dedent("""
        def b(): pass
        class A:
            def m(self): pass
        def a(): pass
    """).lstrip("\n")
    a = PythonParser().parse_file(source=src, repo_id=REPO, file_path="x.py", commit_sha=COMMIT)
    b = PythonParser().parse_file(source=src, repo_id=REPO, file_path="x.py", commit_sha=COMMIT)
    # Same content -> identical unit_ids, identical source_shas, identical order.
    assert [u.unit_id for u in a] == [u.unit_id for u in b]
    assert [u.source_sha for u in a] == [u.source_sha for u in b]


def test_syntax_error_raises() -> None:
    with pytest.raises(SyntaxError):
        PythonParser().parse_file(
            source="def broken(:\n", repo_id=REPO, file_path="x.py", commit_sha=COMMIT
        )


def test_unit_id_matches_stable_helper() -> None:
    from schemas import stable_unit_id

    units = _parse("def thing(): pass\n")
    fn = next(u for u in units if u.name == "thing")
    assert fn.unit_id == stable_unit_id(REPO, "pkg/mod.py", "pkg.mod.thing")
