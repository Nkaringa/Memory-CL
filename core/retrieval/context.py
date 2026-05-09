from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


def _query_id(text: str, repo_id: str) -> str:
    """Deterministic query id used in spans + structured logs.

    A pure hash means the same query string under the same tenant
    always shares a single id across runs, which is exactly what
    determinism requires.
    """
    return hashlib.sha256(f"{repo_id}\x00{text}".encode()).hexdigest()[:16]


@dataclass(slots=True)
class RetrievalContext:
    """Runtime context threaded through the Phase-4 pipeline.

    The context holds the live storage clients (or mocks in tests) so
    every layer below `apps/` can stay free of any imports from the
    composition root.
    """

    repo_id: str
    query_text: str
    query_id: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.query_id:
            self.query_id = _query_id(self.query_text, self.repo_id)
