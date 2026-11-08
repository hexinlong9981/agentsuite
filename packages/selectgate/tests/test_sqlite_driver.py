import pytest

from selectgate.core.drivers.sqlite_driver import SqliteDriver
from selectgate.core.errors import QueryTimeout


def test_full_scan_estimate(db):
    d = SqliteDriver(db).connect()
    try:
        est = d.explain("SELECT * FROM item WHERE v > 0")   # no index on v
        assert "item" in est.full_scans
        assert est.max_scanned_rows >= 50
    finally:
        d.close()


def test_indexed_search_estimate(db):
    d = SqliteDriver(db).connect()
    try:
        est = d.explain("SELECT * FROM item WHERE k = 'k10'")  # uses idx_k
        assert est.full_scans == []
        assert est.max_scanned_rows < 50
    finally:
        d.close()


def test_execute_truncates(db):
    d = SqliteDriver(db).connect()
    try:
        res = d.execute("SELECT * FROM item", max_rows=5, timeout=5)
        assert res.rowcount == 5 and res.truncated is True
        assert "id" in res.columns
    finally:
        d.close()


def test_timeout_interrupts(db):
    d = SqliteDriver(db).connect()
    try:
        # count(*) over an infinite recursive CTE never returns -> interrupted
        sql = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
               "SELECT count(*) FROM c")
        with pytest.raises(QueryTimeout):
            d.execute(sql, max_rows=1, timeout=0.5)
    finally:
        d.close()
