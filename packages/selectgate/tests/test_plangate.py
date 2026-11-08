import pytest

from selectgate.core.config import Limits
from selectgate.core.errors import PlanGateExceeded
from selectgate.core.plangate import evaluate
from selectgate.core.types import PlanEstimate


def test_within_budget_ok():
    evaluate(PlanEstimate(max_scanned_rows=5, full_scans=[], plan=[]), Limits(max_scanned_rows=10))


def test_over_budget_rejected():
    with pytest.raises(PlanGateExceeded):
        evaluate(PlanEstimate(max_scanned_rows=50, full_scans=["item"], plan=[]),
                 Limits(max_scanned_rows=10))
