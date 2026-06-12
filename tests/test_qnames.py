from __future__ import annotations

from core.parsing.qnames import module_qname_from_path


def test_python_paths() -> None:
    assert module_qname_from_path("pkg/mod.py") == "pkg.mod"
    assert module_qname_from_path("pkg/__init__.py") == "pkg"
    assert module_qname_from_path("top.py") == "top"
    # `index.py` must NOT collapse — index-collapse is a JS-world rule.
    assert module_qname_from_path("pkg/index.py") == "pkg.index"


def test_js_ts_paths() -> None:
    assert module_qname_from_path("src/app.js") == "src.app"
    assert module_qname_from_path("src/components/Button.tsx") == "src.components.Button"
    assert module_qname_from_path("src/utils/index.js") == "src.utils"
    assert module_qname_from_path("src/utils/index.ts") == "src.utils"
    assert module_qname_from_path("lib/loader.cjs") == "lib.loader"
    assert module_qname_from_path("lib/esm.mjs") == "lib.esm"
    # `__init__.js` is not a Python file — no collapse.
    assert module_qname_from_path("src/__init__.js") == "src.__init__"


def test_suffixless_paths_collapse_index() -> None:
    # Import-resolution calls this with already-stripped paths.
    assert module_qname_from_path("src/utils/index") == "src.utils"
    assert module_qname_from_path("src/utils") == "src.utils"


def test_root_level_index_and_init_do_not_collapse_to_empty() -> None:
    assert module_qname_from_path("index.js") == "index"
    assert module_qname_from_path("__init__.py") == "__init__"


def test_doc_paths() -> None:
    assert module_qname_from_path("docs/setup.md") == "docs.setup"
    assert module_qname_from_path("README.md") == "README"
    assert module_qname_from_path("docs/page.mdx") == "docs.page"
    assert module_qname_from_path("docs/manual.rst") == "docs.manual"
    assert module_qname_from_path("notes/todo.txt") == "notes.todo"
    # No index/README collapse for docs — qnames stay purely path-based.
    assert module_qname_from_path("docs/index.md") == "docs.index"
    assert module_qname_from_path("docs/README.md") == "docs.README"


def test_graph_builder_mirror_stays_in_sync() -> None:
    """graph_builder inlines a mirror of module_qname_from_path on purpose
    (narrow import surface). This test fails if the two ever drift."""
    from core.ingestion.graph_builder import _module_qname

    for path in (
        "pkg/mod.py", "pkg/__init__.py", "pkg/index.py",
        "src/app.js", "src/utils/index.ts", "src/components/Button.tsx",
        "lib/loader.cjs", "top.py", "index.js", "__init__.py",
        "pkg/server/handler.go", "Assets/Scripts/Player.cs",
        "src/main/java/App.java", "src/db/mod.rs", "mod.rs", "pkg/index.go",
        "docs/setup.md", "README.md", "docs/page.mdx", "docs/manual.rst",
        "notes/todo.txt", "docs/index.md",
    ):
        assert _module_qname(path) == module_qname_from_path(path), path
