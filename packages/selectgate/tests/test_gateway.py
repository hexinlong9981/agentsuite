import pytest

from selectgate import core
from selectgate.core.errors import (
    PlanGateExceeded,
    StatementRejected,
    TargetNotAllowed,
)


def test_indexed_query_passes(config):
    res, est = core.run_select("main", "SELECT * FROM item WHERE k = 'k10'", config=config)
    assert res.rowcount == 1
    assert est.max_scanned_rows <= config.limits.max_scanned_rows


def test_full_scan_blocked_by_plan_gate(config):
    # limits.max_scanned_rows == 10, table has 50 rows -> full scan refused
    with pytest.raises(PlanGateExceeded):
        core.run_select("main", "SELECT * FROM item WHERE v > 0", config=config)


def test_full_scan_allowed_with_higher_budget(config_factory):
    cfg = config_factory(max_scanned_rows=100000, max_rows=5)
    res, est = core.run_select("main", "SELECT * FROM item WHERE v > 0", config=cfg)
    assert res.truncated is True and res.rowcount == 5
    assert "item" in est.full_scans


def test_unknown_target_rejected(config):
    with pytest.raises(TargetNotAllowed):
        core.run_select("nope", "SELECT 1", config=config)


def test_write_rejected(config):
    with pytest.raises(StatementRejected):
        core.run_select("main", "DELETE FROM item", config=config)


def test_explain_query(config):
    est = core.explain_query("main", "SELECT * FROM item WHERE v > 0", config=config)
    assert "item" in est.full_scans
