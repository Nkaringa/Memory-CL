from __future__ import annotations

import textwrap

from core.parsing import TreeSitterParser
from schemas import Language, UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"


def _parse(source: str, file_path: str = "src/app.js") -> list:
    lang = (
        Language.TYPESCRIPT
        if file_path.endswith((".ts", ".tsx", ".mts", ".cts"))
        else Language.JAVASCRIPT
    )
    return TreeSitterParser(lang).parse_file(
        source=textwrap.dedent(source).lstrip("\n"),
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )


def test_module_unit_first_with_full_source() -> None:
    units = _parse("const x = 1;\n")
    assert units[0].kind == UnitKind.MODULE
    assert units[0].qualified_name == "src.app"
    assert units[0].name == "app"
    assert units[0].language == Language.JAVASCRIPT
    assert units[0].content == "const x = 1;\n"
    assert units[0].line_start == 1


def test_index_file_collapses_module_qname() -> None:
    units = _parse("const x = 1;\n", file_path="src/utils/index.js")
    assert units[0].qualified_name == "src.utils"
    assert units[0].name == "utils"


def test_typescript_module_language() -> None:
    units = _parse("const x: number = 1;\n", file_path="src/lib.ts")
    assert units[0].language == Language.TYPESCRIPT


def test_syntax_error_still_returns_module_unit() -> None:
    # Broken function followed by a healthy const — error-tolerant parse.
    units = _parse("function broken( { if (x {\nconst X = 1;\n")
    assert units[0].kind == UnitKind.MODULE


def test_module_docstring_from_leading_block_comment() -> None:
    units = _parse("""
        /* App entry point. */
        const x = 1;
    """)
    assert units[0].docstring == "App entry point."
