from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Sentinel kept here for clarity; do NOT export — outside callers should
# pass `None` or omit values rather than rely on a sentinel object.
_OMIT = object()


def compact_payload(payload: Mapping[str, Any], *, drop_zero: bool = False) -> dict[str, Any]:
    """Produce the minimum-token equivalent of `payload`.

    Drops, in order:
        1. Keys whose value is None
        2. Keys whose value is an empty list / tuple / dict / str
        3. (optionally) Keys whose value is exactly 0 / 0.0 / False

    The result is a fresh dict; the input is never mutated. Recursion is
    intentionally NOT applied: the dense schemas are flat by spec, and
    recursing would risk reordering nested structures the caller has
    already sorted deliberately.

    Determinism is preserved because:
    - dropping keys never reorders survivors
    - the rules above are pure (no clock, no PRNG)
    """
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple, dict, str)) and len(v) == 0:
            continue
        if drop_zero and v in (0, 0.0, False):
            continue
        out[k] = v
    return out
