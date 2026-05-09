"""Boot-time health gate.

Runs each `BootStage` in order; collects the per-stage outcome.
Returns a `HealthGateOutcome` aggregating the results. The gate is
deterministic given the same probe results (probes are pure function
inputs) — replay is straightforward.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum


class StageStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BootStage:
    """One step in the deterministic startup sequence."""

    name: str
    order: int                                  # lower runs first
    probe: Callable[[], Awaitable[bool]]        # async probe; True == pass
    required: bool = True                       # if False → DEGRADED on fail
    description: str = ""


@dataclass(frozen=True, slots=True)
class StageResult:
    name: str
    order: int
    status: StageStatus
    error: str = ""


@dataclass(frozen=True, slots=True)
class HealthGateOutcome:
    overall_ok: bool
    safe_mode_recommended: bool
    results: tuple[StageResult, ...]
    failed_stages: tuple[str, ...] = field(default_factory=tuple)
    degraded_stages: tuple[str, ...] = field(default_factory=tuple)


class HealthGate:
    """Sequential probe runner for the spec'd 8-stage boot sequence."""

    def __init__(self, stages: list[BootStage]) -> None:
        names = [s.name for s in stages]
        if len(names) != len(set(names)):
            raise ValueError("duplicate stage names")
        self._stages = sorted(stages, key=lambda s: (s.order, s.name))

    async def run(self) -> HealthGateOutcome:
        results: list[StageResult] = []
        failed: list[str] = []
        degraded: list[str] = []
        for stage in self._stages:
            try:
                ok = await stage.probe()
                if ok:
                    status = StageStatus.OK
                elif stage.required:
                    status = StageStatus.FAILED
                    failed.append(stage.name)
                else:
                    status = StageStatus.DEGRADED
                    degraded.append(stage.name)
                results.append(
                    StageResult(name=stage.name, order=stage.order, status=status)
                )
            except Exception as exc:
                status = (
                    StageStatus.FAILED if stage.required else StageStatus.DEGRADED
                )
                if stage.required:
                    failed.append(stage.name)
                else:
                    degraded.append(stage.name)
                results.append(
                    StageResult(
                        name=stage.name, order=stage.order,
                        status=status, error=f"{type(exc).__name__}: {exc}",
                    )
                )
        # Safe mode is recommended whenever any stage failed OR more
        # than one stage degraded — the latter signals systemic trouble.
        safe_mode = bool(failed) or len(degraded) > 1
        return HealthGateOutcome(
            overall_ok=not failed,
            safe_mode_recommended=safe_mode,
            results=tuple(results),
            failed_stages=tuple(sorted(failed)),
            degraded_stages=tuple(sorted(degraded)),
        )


__all__ = [
    "BootStage",
    "HealthGate",
    "HealthGateOutcome",
    "StageResult",
    "StageStatus",
]
