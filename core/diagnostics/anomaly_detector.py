"""Statistical anomaly detection over an observation series.

Reports outliers using a z-score threshold. Pure deterministic
function — no PRNG, no clock. Designed to take Phase-7 latency or
throughput samples and surface the bad ones for an operator to
investigate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class AnomalySeverity(StrEnum):
    NORMAL = "normal"
    WATCH = "watch"
    ANOMALY = "anomaly"


@dataclass(frozen=True, slots=True)
class AnomalyReport:
    severity: AnomalySeverity
    sample_count: int
    mean: float
    stdev: float
    outlier_indices: tuple[int, ...] = field(default_factory=tuple)
    outlier_values: tuple[float, ...] = field(default_factory=tuple)
    threshold_z: float = 3.0


class AnomalyDetector:
    def __init__(self, *, z_threshold: float = 3.0) -> None:
        if z_threshold <= 0:
            raise ValueError("z_threshold must be > 0")
        self._z = z_threshold

    def analyze(self, samples: Sequence[float]) -> AnomalyReport:
        n = len(samples)
        if n == 0:
            return AnomalyReport(
                severity=AnomalySeverity.NORMAL,
                sample_count=0, mean=0.0, stdev=0.0,
            )
        mean = sum(samples) / n
        if n < 2:
            return AnomalyReport(
                severity=AnomalySeverity.NORMAL,
                sample_count=n, mean=mean, stdev=0.0,
            )
        var = sum((x - mean) ** 2 for x in samples) / (n - 1)
        stdev = math.sqrt(var)
        if stdev == 0:
            return AnomalyReport(
                severity=AnomalySeverity.NORMAL,
                sample_count=n, mean=mean, stdev=0.0,
            )
        outlier_pairs = [
            (i, x) for i, x in enumerate(samples)
            if abs(x - mean) / stdev >= self._z
        ]
        # WATCH band: above 2-sigma but below z_threshold.
        watch_pairs = [
            (i, x) for i, x in enumerate(samples)
            if 2.0 <= abs(x - mean) / stdev < self._z
        ]
        if outlier_pairs:
            severity = AnomalySeverity.ANOMALY
        elif watch_pairs:
            severity = AnomalySeverity.WATCH
        else:
            severity = AnomalySeverity.NORMAL
        return AnomalyReport(
            severity=severity,
            sample_count=n,
            mean=mean,
            stdev=stdev,
            outlier_indices=tuple(i for i, _ in outlier_pairs),
            outlier_values=tuple(round(v, 6) for _, v in outlier_pairs),
            threshold_z=self._z,
        )


__all__ = ["AnomalyDetector", "AnomalyReport", "AnomalySeverity"]
