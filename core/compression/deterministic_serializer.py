from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


def _normalize(obj: Any) -> Any:
    """Convert an arbitrary value into JSON-serializable form deterministically.

    Lists/tuples are kept as-is (caller is expected to have sorted them
    where the spec requires); dicts get key-sorted recursively; pydantic
    models are dumped via `model_dump(mode="json")`.
    """
    if isinstance(obj, BaseModel):
        return _normalize(obj.model_dump(mode="json"))
    if isinstance(obj, Mapping):
        return {k: _normalize(obj[k]) for k in sorted(obj.keys(), key=str)}
    if isinstance(obj, (list, tuple)):
        return [_normalize(x) for x in obj]
    return obj


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8.

    Same input → same bytes, always. Independent of insertion order in
    input dicts, of pydantic field-declaration order, or of OS locale.
    """
    return json.dumps(
        _normalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def canonical_bytes(obj: Any) -> bytes:
    """UTF-8 encoded canonical JSON."""
    return canonical_json(obj).encode("utf-8")
