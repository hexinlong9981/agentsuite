from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class MetricRef:
    source: str
    name: str
    namespace: str = ""
    dims: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DataPoint:
    ts: int                 # epoch seconds
    value: float | None


@dataclass(frozen=True)
class Series:
    ref: MetricRef
    period: int             # seconds between points
    points: list[DataPoint] = field(default_factory=list)


@dataclass(frozen=True)
class FetchPlan:
    period: int
    expected_points: int
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FetchResult:
    series: Series
    plan: FetchPlan
    csv_path: str
    points: int


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
