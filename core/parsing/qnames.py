from __future__ import annotations

# Source-file suffixes the ingestion layer understands, used both for
# qname derivation and import-specifier normalization. Order matters
# only in that every entry must be matched by exact `endswith`.
SOURCE_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".jsx", ".mjs", ".cjs", ".js",
    ".tsx", ".mts", ".cts", ".ts",
    ".cs", ".go", ".java", ".rs",
    # Documentation files (docs ingestion). `docs/setup.md` -> `docs.setup`;
    # no `index`/`README` collapse — doc qnames stay purely path-based.
    ".mdx", ".md", ".rst", ".txt",
)

# Suffixes whose `index` basename collapses onto the parent directory.
# Suffixless paths (only the JS import resolver produces those) collapse
# too, hence the membership test below is on the *matched* suffix.
_INDEX_COLLAPSE_SUFFIXES: frozenset[str | None] = frozenset({
    ".jsx", ".mjs", ".cjs", ".js",
    ".tsx", ".mts", ".cts", ".ts",
    None,
})


def module_qname_from_path(file_path: str) -> str:
    """Convert a repo-relative POSIX path to a dotted module qname.

    Examples:
        "pkg/mod.py"          -> "pkg.mod"
        "pkg/__init__.py"     -> "pkg"
        "src/app.js"          -> "src.app"
        "src/utils/index.ts"  -> "src.utils"
        "src/utils/index"     -> "src.utils"   (suffix already stripped)
        "index.js"            -> "index"      (no collapse at repo root)
        "pkg/server/handler.go" -> "pkg.server.handler"
        "src/db/mod.rs"       -> "src.db"     (Rust mod.rs ≈ index.js)

    `__init__` collapse applies only to Python files; `index` collapse
    applies only to JS/TS files (and suffixless paths, which only the
    JS import resolver produces); `mod` collapse applies only to Rust
    files. C#/Go/Java qnames are purely path-based — design D-16.
    """
    matched: str | None = None
    stem_path = file_path
    for suffix in SOURCE_SUFFIXES:
        if file_path.endswith(suffix):
            stem_path = file_path[: -len(suffix)]
            matched = suffix
            break
    parts = stem_path.split("/")
    if len(parts) > 1 and (
        (matched == ".py" and parts[-1] == "__init__")
        or (matched == ".rs" and parts[-1] == "mod")
        or (matched in _INDEX_COLLAPSE_SUFFIXES and parts[-1] == "index")
    ):
        parts = parts[:-1]
    return ".".join(parts)
