"""Synthetic source — deterministic series. For demos and offline tests."""

from __future__ import annotations

import math

from ..types import DataPoint, MetricRef, Series
from . import MetricsSource


class SyntheticSource(MetricsSource):
    def __init__(self, name: str, options: dict) -> None:
        self.name = name
        self.metrics = list(options.get("metrics", ["cpu", "mem"]))

    def list_metrics(self) -> list[MetricRef]:
        return [MetricRef(self.name, m) for m in self.metrics]

    def fetch(self, ref: MetricRef, start: int, end: int, period: int) -> Series:
        points: list[DataPoint] = []
        ts = start
        while ts < end:
            value = round(50 + 50 * math.sin(ts / 3600.0), 3)
            points.append(DataPoint(ts=ts, value=value))
            ts += period
        return Series(ref=ref, period=period, points=points)
