"""Plan gate — refuse queries whose EXPLAIN estimate exceeds the row budget."""

from __future__ import annotations

from .config import Limits
from .errors import PlanGateExceeded
from .types import PlanEstimate


def evaluate(estimate: PlanEstimate, limits: Limits) -> None:
    if estimate.max_scanned_rows > limits.max_scanned_rows:
        hint = (
            f"full table scan(s): {estimate.full_scans}; add an index or a more "
            "selective WHERE/LIMIT"
            if estimate.full_scans
            else "reduce the scanned set"
        )
        raise PlanGateExceeded(
            f"estimated scan of {estimate.max_scanned_rows} rows exceeds the limit "
            f"of {limits.max_scanned_rows} ({hint})"
        )
