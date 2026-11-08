import json
from unittest.mock import MagicMock

import pytest

from selectgate.core.drivers import get_driver
from selectgate.core.drivers.mysql_driver import MysqlDriver, parse_mysql_plan
from selectgate.core.drivers.postgres_driver import PostgresDriver, parse_postgres_plan


def test_driver_registry():
    assert get_driver("postgres") is PostgresDriver
    assert get_driver("postgresql") is PostgresDriver
    assert get_driver("mysql") is MysqlDriver
    assert get_driver("mariadb") is MysqlDriver


def test_parse_postgres_plan_seq_scan():
    raw_plan = [
        {
            "Plan": {
                "Node Type": "Seq Scan",
                "Relation Name": "large_orders",
                "Alias": "large_orders",
                "Startup Cost": 0.0,
                "Total Cost": 450.0,
                "Plan Rows": 15000,
                "Plan Width": 32,
            }
        }
    ]
    estimate = parse_postgres_plan(raw_plan)
    assert estimate.max_scanned_rows == 15000
    assert "large_orders" in estimate.full_scans
    assert len(estimate.plan) == 1
    assert "Seq Scan on large_orders" in estimate.plan[0]


def test_parse_postgres_plan_nested_index():
    raw_plan = [
        {
            "Plan": {
                "Node Type": "Nested Loop",
                "Plan Rows": 10,
                "Plans": [
                    {
                        "Node Type": "Index Scan",
                        "Relation Name": "users",
                        "Plan Rows": 1,
                    },
                    {
                        "Node Type": "Index Scan",
                        "Relation Name": "orders",
                        "Plan Rows": 5,
                    },
                ],
            }
        }
    ]
    estimate = parse_postgres_plan(raw_plan)
    assert len(estimate.full_scans) == 0
    # Root 10 + index scan 1 + index scan 5 = 16
    assert estimate.max_scanned_rows == 16


def test_parse_mysql_plan_all_scan():
    raw_plan = {
        "query_block": {
            "select_id": 1,
            "table": {
                "table_name": "payments",
                "access_type": "ALL",
                "rows_examined_per_scan": 20000,
            },
        }
    }
    estimate = parse_mysql_plan(raw_plan)
    assert estimate.max_scanned_rows == 20000
    assert "payments" in estimate.full_scans
    assert len(estimate.plan) == 1
    assert "access=ALL" in estimate.plan[0]


def test_parse_mysql_plan_index():
    raw_plan = {
        "query_block": {
            "select_id": 1,
            "table": {
                "table_name": "payments",
                "access_type": "ref",
                "rows_examined_per_scan": 5,
            },
        }
    }
    estimate = parse_mysql_plan(raw_plan)
    assert len(estimate.full_scans) == 0
    assert estimate.max_scanned_rows == 5


def test_postgres_driver_mock_execution():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    plan_json = [
        {"Plan": {"Node Type": "Seq Scan", "Relation Name": "accounts", "Plan Rows": 100}}
    ]
    mock_cursor.fetchone.return_value = (json.dumps(plan_json),)

    driver = PostgresDriver("postgresql://localhost/testdb", raw_conn=mock_conn)
    est = driver.explain("SELECT * FROM accounts")
    assert est.max_scanned_rows == 100
    assert "accounts" in est.full_scans

    # Mock execute read
    mock_cursor.description = [("id",), ("name",)]
    mock_cursor.fetchmany.return_value = [(1, "alice"), (2, "bob")]
    res = driver.execute("SELECT id, name FROM accounts", max_rows=10, timeout=5.0)
    assert res.columns == ["id", "name"]
    assert len(res.rows) == 2
    assert not res.truncated

    # Mock execute write
    mock_cursor.rowcount = 1
    wres = driver.execute_write("UPDATE accounts SET name = 'charlie' WHERE id = 1", timeout=5.0)
    assert wres.rowcount == 1
    assert wres.statement == "update"
    mock_conn.commit.assert_called_once()


def test_mysql_driver_execute_applies_timeout():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.description = [("id",)]
    mock_cursor.fetchmany.return_value = [(1,)]

    driver = MysqlDriver("mysql://localhost/testdb", raw_conn=mock_conn)
    res = driver.execute("SELECT id FROM t", max_rows=10, timeout=3.0)
    assert res.rows == [[1]]

    executed = [c.args[0] for c in mock_cursor.execute.call_args_list]
    assert any("MAX_EXECUTION_TIME" in s for s in executed)
    assert "SELECT id FROM t" in executed


def test_mysql_driver_mock_execution():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    plan_json = {
        "query_block": {
            "table": {"table_name": "items", "access_type": "ALL", "rows_examined_per_scan": 300}
        }
    }
    mock_cursor.fetchone.return_value = (json.dumps(plan_json),)

    driver = MysqlDriver("mysql://localhost/testdb", raw_conn=mock_conn)
    est = driver.explain("SELECT * FROM items")
    assert est.max_scanned_rows == 300
    assert "items" in est.full_scans

    # Mock execute write
    mock_cursor.rowcount = 3
    wres = driver.execute_write("DELETE FROM items WHERE old = 1", timeout=5.0)
    assert wres.rowcount == 3
    assert wres.statement == "delete"
    mock_conn.commit.assert_called_once()
