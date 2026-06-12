"""Batch-2 skeleton coverage (C#, Go, Java, Rust) — Task 1 of the
multilang batch-2 plan.

Only the NEW behavior is tested here: enum values, qname rows, the
dispatcher accepting the new languages and emitting a module unit.
JS/TS extraction behavior is pinned by tests/test_treesitter_parser.py,
which this task must leave untouched and green.
"""

from __future__ import annotations

import pytest

from core.ingestion.pipeline import _default_parsers
from core.parsing import TreeSitterParser
from core.parsing.qnames import module_qname_from_path
from schemas import Language, UnitKind

REPO = "test-repo"
COMMIT = "deadbeef"

_TRIVIAL_SOURCES: dict[Language, tuple[str, str]] = {
    # language: (file_path, source)
    Language.CSHARP: ("src/Player.cs", "class Player {}\n"),
    Language.GO: ("pkg/server/handler.go", "package main\n"),
    Language.JAVA: ("src/main/App.java", "class App {}\n"),
    Language.RUST: ("src/lib.rs", "fn main() {}\n"),
}


# ---------------------------------------------------------------------------
# Language enum
# ---------------------------------------------------------------------------
def test_language_enum_has_batch2_values() -> None:
    assert Language.CSHARP == "csharp"
    assert Language.GO == "go"
    assert Language.JAVA == "java"
    assert Language.RUST == "rust"


# ---------------------------------------------------------------------------
# Module qnames (path-based, design D-16)
# ---------------------------------------------------------------------------
def test_batch2_module_qnames_strip_extension() -> None:
    assert module_qname_from_path("src/Player.cs") == "src.Player"
    assert module_qname_from_path("pkg/server/handler.go") == "pkg.server.handler"
    assert module_qname_from_path("src/main/App.java") == "src.main.App"
    assert module_qname_from_path("src/lib.rs") == "src.lib"


def test_rust_mod_rs_collapses_like_index_js() -> None:
    assert module_qname_from_path("src/db/mod.rs") == "src.db"
    # No collapse at repo root — mirrors the index.js rule.
    assert module_qname_from_path("mod.rs") == "mod"
    # `mod` collapse is Rust-only.
    assert module_qname_from_path("pkg/mod.go") == "pkg.mod"


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("language", list(_TRIVIAL_SOURCES))
def test_dispatcher_accepts_batch2_language(language: Language) -> None:
    file_path, source = _TRIVIAL_SOURCES[language]
    units = TreeSitterParser(language).parse_file(
        source=source,
        repo_id=REPO,
        file_path=file_path,
        commit_sha=COMMIT,
    )
    # Dispatcher contract only: module unit first, correct identity.
    # Per-language extraction behavior is pinned in tests/test_<lang>_parser.py.
    assert len(units) >= 1
    mod = units[0]
    assert mod.kind == UnitKind.MODULE
    assert mod.language == language
    assert mod.qualified_name == module_qname_from_path(file_path)
    assert mod.content == source


def test_dispatcher_rejects_python() -> None:
    with pytest.raises(ValueError, match="does not handle"):
        TreeSitterParser(Language.PYTHON)


def test_default_parsers_cover_batch2_languages() -> None:
    parsers = _default_parsers()
    for language in _TRIVIAL_SOURCES:
        assert language in parsers
        assert isinstance(parsers[language], TreeSitterParser)
