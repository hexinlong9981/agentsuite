from pathlib import Path

import pytest

from metrics_fetcher import core
from metrics_fetcher.core.errors import InvalidRange


def test_list_metrics(config):
    assert {r.name for r in core.list_metrics("syn", config=config)} == {"cpu", "mem"}
    csv_refs = core.list_metrics("files", config=config)
    assert any(r.name == "cpu" and r.namespace == "host1" for r in csv_refs)


def test_fetch_synthetic_writes_csv(config):
    res = core.fetch("syn", "cpu", 0, 3600, config=config)
    assert res.points == 60 and res.plan.period == 60
    p = Path(res.csv_path)
    assert p.is_file()
    assert p.read_text().splitlines()[0] == "epoch,iso,value"


def test_fetch_csv_source(config):
    res = core.fetch("files", "cpu", 1000, 2200, config=config, namespace="host1")
    assert res.points >= 1


def test_no_write(config):
    res = core.fetch("syn", "cpu", 0, 600, config=config, write=False)
    assert not Path(res.csv_path).exists()


def test_invalid_range(config):
    with pytest.raises(InvalidRange):
        core.fetch("syn", "cpu", 10, 10, config=config)
