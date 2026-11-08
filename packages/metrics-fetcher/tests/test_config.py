import pytest

from metrics_fetcher.core import Config
from metrics_fetcher.core.errors import SourceNotAllowed


def test_fail_closed_source():
    with pytest.raises(SourceNotAllowed):
        Config.from_dict({}).get_source("x")


def test_bad_max_points_rejected():
    with pytest.raises(ValueError):
        Config.from_dict({"output": {"max_points": 0}})
