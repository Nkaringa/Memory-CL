from __future__ import annotations

from collections.abc import Iterable
from typing import get_args

from schemas import ContextEntry, ContextEntryType

# Priority order mandated by Phase-4 CONTEXT_ASSEMBLY_RULES:
# constraints > risks > architecture > logic > code.
_PRIORITY_ORDER: tuple[ContextEntryType, ...] = get_args(ContextEntryType)
# `get_args` of `Literal[...]` returns the values in declaration order.
# We declared them in the desired priority order in `schemas/retrieval.py`.
_PRIORITY_INDEX: dict[ContextEntryType, int] = {
    t: i for i, t in enumerate(_PRIORITY_ORDER)
}


def _approx_tokens(entry: ContextEntry) -> int:
    """Cheap token estimate (4 chars/token, matching Phase-3 chunker).

    A precise tokenizer would be more accurate but a) deterministic and
    b) unnecessary for budget enforcement at the packet level.
    """
    text_len = len(entry.id)
    for v in entry.data.values():
        text_len += len(str(v))
    return (text_len + 3) // 4


class ContextOptimizer:
    """Trim a context list to fit a token budget without breaking determinism.

    Algorithm:
        1. Deduplicate by entry.id, keeping the highest-scoring instance.
        2. Sort by (priority_index ASC, -score, id ASC) — priority groups
           stay together; high-score entries within a group win ties; id
           is the final deterministic tie-breaker.
        3. Greedily admit entries until the cumulative token estimate
           would exceed the budget.

    Same input → same output, independent of Python set iteration
    order (the dedup pass uses dict-by-id so insertion order doesn't
    matter; the explicit sort handles the rest).
    """

    def __init__(self, *, max_tokens: int) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be > 0")
        self._max_tokens = max_tokens

    def optimize(self, entries: Iterable[ContextEntry]) -> list[ContextEntry]:
        # Deduplicate: highest score wins on collision.
        by_id: dict[str, ContextEntry] = {}
        for e in entries:
            existing = by_id.get(e.id)
            if existing is None or e.score > existing.score:
                by_id[e.id] = e

        ordered = sorted(
            by_id.values(),
            key=lambda e: (_PRIORITY_INDEX[e.type], -e.score, e.id),
        )

        budget = self._max_tokens
        out: list[ContextEntry] = []
        for e in ordered:
            cost = _approx_tokens(e)
            if cost > budget:
                # Don't break — a later, smaller, higher-priority entry
                # may still fit. But we MUST not advance budget; we
                # simply skip and continue.
                continue
            out.append(e)
            budget -= cost
        return out
