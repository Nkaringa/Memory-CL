"""Rust extraction rules — STUB, filled in by Task 5 of the multilang
batch-2 plan (docs/superpowers/plans/2026-06-12-multilang-batch2-*.md).

Until then the dispatcher emits only the module unit for `.rs` files:
both extractors return empty lists and there is no module docstring.
"""

from __future__ import annotations

from tree_sitter import Node

from core.parsing.languages._shared import _ParseInputs
from schemas import IngestionUnit


def module_docstring(root: Node) -> str | None:
    return None


def extract_children(root: Node, inputs: _ParseInputs) -> list[IngestionUnit]:
    return []


def extract_imports(root: Node, inputs: _ParseInputs) -> list[str]:
    return []
