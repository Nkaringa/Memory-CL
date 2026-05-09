"""Environment-driven feature flag registry.

Phase 1 already exposes individual feature toggles via `Settings`.
Phase 9 adds a registry that:
    * names + describes each flag (for /status surfacing)
    * supports per-environment overrides
    * is fully read-only at runtime (boot-time configuration only)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FeatureFlag:
    name: str
    description: str
    enabled: bool
    environment: str  # "development" | "staging" | "production"


class FeatureFlagRegistry:
    """Immutable view of every flag the runtime acknowledges."""

    def __init__(self, flags: list[FeatureFlag]) -> None:
        self._flags: dict[str, FeatureFlag] = {}
        for flag in flags:
            if flag.name in self._flags:
                raise ValueError(f"duplicate flag '{flag.name}'")
            self._flags[flag.name] = flag

    @classmethod
    def from_settings(cls, settings: object) -> FeatureFlagRegistry:
        env = getattr(settings, "environment", "development")
        return cls([
            FeatureFlag(
                name="enable_graph_ranking",
                description="Phase-4 graph_proximity contributes to ranking",
                enabled=bool(getattr(settings, "enable_graph_ranking", True)),
                environment=env,
            ),
            FeatureFlag(
                name="enable_incremental_indexing",
                description="Re-ingest incrementally on changed files only",
                enabled=bool(getattr(settings, "enable_incremental_indexing", True)),
                environment=env,
            ),
            FeatureFlag(
                name="enable_context_compression",
                description="Apply Phase-3 dense compression on retrieval",
                enabled=bool(getattr(settings, "enable_context_compression", True)),
                environment=env,
            ),
            FeatureFlag(
                name="ui_enabled",
                description="Mount the read-only Phase-9 inspection UI",
                enabled=bool(getattr(settings, "ui_enabled", True)),
                environment=env,
            ),
            FeatureFlag(
                name="strict_bootstrap",
                description="Fail startup if any boot stage reports degraded",
                enabled=bool(getattr(settings, "strict_bootstrap", False)),
                environment=env,
            ),
        ])

    def get(self, name: str) -> FeatureFlag:
        return self._flags[name]

    def is_enabled(self, name: str) -> bool:
        return self._flags[name].enabled

    def all(self) -> list[FeatureFlag]:
        return [self._flags[k] for k in sorted(self._flags)]


__all__ = ["FeatureFlag", "FeatureFlagRegistry"]
