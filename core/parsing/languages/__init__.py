"""Per-language tree-sitter extraction modules.

Each module implements the same public interface, dispatched by
`core.parsing.treesitter_parser.TreeSitterParser`:

    extract_children(root, inputs) -> list[IngestionUnit]
    extract_imports(root, inputs)  -> list[str]
    module_docstring(root)         -> str | None

Shared, language-agnostic helpers live in `_shared`.
"""
