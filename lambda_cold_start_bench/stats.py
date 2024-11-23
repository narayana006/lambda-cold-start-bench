"""Summary statistics for groups of invocations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .report import Report


def percentile(values: Sequence[float], pct: float) -> float:
    """Percentile with linear interpolation between the two nearest ranks.

    This is the same definition as numpy's default: for n sorted values the
    position is ``(n - 1) * pct / 100``.
    """
    if not values:
        raise ValueError("percentile of an empty sequence")
    if not 0 <= pct <= 100:
        raise ValueError("pct must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


@dataclass(frozen=True)
class Summary:
    n: int
    minimum: float
    mean: float
    p50: float
    p90: float
    p99: float
    maximum: float


def summarize(values: Iterable[float]) -> Optional[Summary]:
    data = list(values)
    if not data:
        return None
    return Summary(
        n=len(data),
        minimum=min(data),
        mean=sum(data) / len(data),
        p50=percentile(data, 50),
        p90=percentile(data, 90),
        p99=percentile(data, 99),
        maximum=max(data),
    )


@dataclass(frozen=True)
class GroupStats:
    """Statistics for one label and memory size."""

    label: str
    memory_size_mb: int
    invocations: int
    init: Optional[Summary]            # Init Duration of cold starts
    cold_duration: Optional[Summary]   # Duration of cold invocations
    warm_duration: Optional[Summary]   # Duration of warm invocations
    cold_billed: Optional[Summary]
    warm_billed: Optional[Summary]
    max_memory_used_mb: int

    @property
    def cold_count(self) -> int:
        return self.init.n if self.init else 0

    @property
    def warm_count(self) -> int:
        return self.warm_duration.n if self.warm_duration else 0


def group_stats(labelled: Iterable[tuple[str, Report]]) -> list[GroupStats]:
    """Group (label, report) pairs by label and configured memory size."""
    groups: dict[tuple[str, int], list[Report]] = {}
    for label, report in labelled:
        groups.setdefault((label, report.memory_size_mb), []).append(report)
    result = []
    for (label, memory), reports in groups.items():
        cold = [r for r in reports if r.is_cold]
        warm = [r for r in reports if not r.is_cold]
        result.append(
            GroupStats(
                label=label,
                memory_size_mb=memory,
                invocations=len(reports),
                init=summarize(r.init_duration_ms for r in cold),
                cold_duration=summarize(r.duration_ms for r in cold),
                warm_duration=summarize(r.duration_ms for r in warm),
                cold_billed=summarize(r.billed_duration_ms for r in cold),
                warm_billed=summarize(r.billed_duration_ms for r in warm),
                max_memory_used_mb=max(r.max_memory_used_mb for r in reports),
            )
        )
    return result
