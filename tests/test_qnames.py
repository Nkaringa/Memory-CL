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
