from __future__ import annotations

# Source-file suffixes the ingestion layer understands, used both for
# qname derivation and import-specifier normalization. Order matters
# only in that every entry must be matched by exact `endswith`.
SOURCE_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".jsx", ".mjs", ".cjs", ".js",
    ".tsx", ".mts", ".cts", ".ts",
)


def module_qname_from_path(file_path: str) -> str:
    """Convert a repo-relative POSIX path to a dotted module qname.

    Examples:
        "pkg/mod.py"          -> "pkg.mod"
        "pkg/__init__.py"     -> "pkg"
        "src/app.js"          -> "src.app"
        "src/utils/index.ts"  -> "src.utils"
        "src/utils/index"     -> "src.utils"   (suffix already stripped)

    `__init__` collapse applies only to Python files; `index` collapse
    applies only to JS/TS files (and suffixless paths, which only the
    JS import resolver produces).
    """
    is_python = False
    stem_path = file_path
    for suffix in SOURCE_SUFFIXES:
        if file_path.endswith(suffix):
            stem_path = file_path[: -len(suffix)]
            is_python = suffix == ".py"
            break
    parts = stem_path.split("/")
    if parts and (
        (is_python and parts[-1] == "__init__")
        or (not is_python and parts[-1] == "index")
    ):
        parts = parts[:-1]
    return ".".join(parts)
