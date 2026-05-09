from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class LifecycleContext:
    """Threaded context for one lifecycle pass.

    Holds the live AppState (so engines can read Redis / Postgres /
    Neo4j without forcing each engine to know how to wire them) plus
    a deterministic `now` snapshot — passing the current time as data
    rather than calling datetime.now() inside engines is what makes
    the same-state-snapshot determinism rule actually achievable.
    """

    repo_id: str
    state: Any  # apps.api.state.AppState — kept loose to avoid up-imports
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    extras: dict[str, Any] = field(default_factory=dict)
