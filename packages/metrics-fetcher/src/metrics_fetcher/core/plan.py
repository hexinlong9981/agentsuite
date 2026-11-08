"""Granularity selection — pick a period so a long range stays legible."""

from __future__ import annotations

import math

from .errors import InvalidRange
from .types import FetchPlan


def choose_period(start: int, end: int, max_points: int, candidate_periods: list[int]) -> FetchPlan:
    if end <= start:
        raise InvalidRange(f"end ({end}) must be greater than start ({start})")
    span = end - start
    periods = sorted(candidate_periods)
    for period in periods:
        points = math.ceil(span / period)
        if points <= max_points:
            return FetchPlan(period=period, expected_points=points, warnings=[])
    # Even the coarsest period exceeds the budget.
    coarsest = periods[-1]
    points = math.ceil(span / coarsest)
    return FetchPlan(
        period=coarsest, expected_points=points,
        warnings=[f"range needs {points} points at the coarsest period ({coarsest}s), "
                  f"over max_points ({max_points}); narrow the range or raise max_points"],
    )
