import pytest

from metrics_fetcher.core import choose_period
from metrics_fetcher.core.errors import InvalidRange


def test_fine_period_when_short():
    p = choose_period(0, 3600, 100, [60, 300, 3600])
    assert p.period == 60 and p.expected_points == 60 and not p.warnings


def test_coarser_period_when_long():
    p = choose_period(0, 86400, 100, [60, 300, 3600])
    assert p.period == 3600            # 86400/3600 = 24 <= 100


def test_warns_when_over_budget():
    p = choose_period(0, 10_000_000, 5, [60, 3600, 86400])
    assert p.period == 86400 and p.warnings


def test_invalid_range():
    with pytest.raises(InvalidRange):
        choose_period(10, 10, 5, [60])
