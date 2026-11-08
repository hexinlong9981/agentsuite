"""MySQL / MariaDB driver with plan-gate support via EXPLAIN FORMAT=JSON.

Read queries are constrained to be read-only *upstream* by the gateway's
statement guard (SELECT-only parse); this driver adds a best-effort server-side
time limit (MySQL ``MAX_EXECUTION_TIME`` / MariaDB ``max_statement_time``).
Write-mode is a separate, explicitly-enabled path (``execute_write``). Supports
pymysql / mysqlclient / mysql-connector when installed, and offline-testable
parser logic for CI/tests.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

from ..errors import DriverError, ExecutionError
from ..types import PlanEstimate, QueryResult, WriteResult
from . import Driver
from .dsn import mysql_kwargs, parse_dsn


def _apply_timeout(cur: Any, timeout: float) -> None:
    """Best-effort server-side execution time limit.

    MySQL 5.7+/8.0 uses ``MAX_EXECUTION_TIME`` (milliseconds, SELECT only);
    MariaDB uses ``max_statement_time`` (seconds, all statements). Try each and
    ignore the one the server does not support, so a runaway query is still
    bounded rather than hanging indefinitely.
    """
    ms = int(timeout * 1000)
    try:
        cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {ms}")
        return
    except Exception:
        pass
    try:
        cur.execute(f"SET SESSION max_statement_time = {float(timeout)}")
    except Exception:
        pass


def parse_mysql_plan(plan_data: dict[str, Any]) -> PlanEstimate:
    """Parse MySQL EXPLAIN FORMAT=JSON output into a PlanEstimate."""
    if not plan_data or not isinstance(plan_data, dict):
        return PlanEstimate(max_scanned_rows=0, full_scans=[], plan=[])

    full_scans: list[str] = []
    plan_lines: list[str] = []
    total_scanned_rows = 0

    def traverse(node: Any) -> None:
        nonlocal total_scanned_rows
        if isinstance(node, dict):
            if "table" in node:
                tbl = node["table"]
                tbl_name = tbl.get("table_name", "")
                access_type = tbl.get("access_type", "")
                rows_examined = int(tbl.get("rows_examined_per_scan", tbl.get("rows", 0)))

                line_desc = f"table {tbl_name} access={access_type} rows={rows_examined}"
                plan_lines.append(line_desc)

                if access_type.upper() == "ALL":
                    if tbl_name:
                        full_scans.append(tbl_name)
                    total_scanned_rows += rows_examined
                elif access_type.upper() in ("INDEX", "RANGE"):
                    total_scanned_rows += max(1, rows_examined)
                else:
                    total_scanned_rows += rows_examined

            for v in node.values():
                traverse(v)
        elif isinstance(node, list):
            for item in node:
                traverse(item)

    query_block = plan_data.get("query_block", plan_data)
    traverse(query_block)

    return PlanEstimate(
        max_scanned_rows=total_scanned_rows,
        full_scans=full_scans,
        plan=plan_lines,
    )


class MysqlDriver(Driver):
    """MySQL / MariaDB driver adapter."""

    def __init__(self, dsn: str, *, raw_conn: Any = None) -> None:
        super().__init__(dsn)
        self._conn = raw_conn
        self._driver_module: Any = None

    def _get_module(self) -> Any:
        if self._driver_module is not None:
            return self._driver_module
        for mod_name in ("pymysql", "MySQLdb", "mysql.connector"):
            try:
                mod = importlib.import_module(mod_name)
                self._driver_module = mod
                return mod
            except ImportError:
                continue
        raise DriverError(
            "no MySQL driver installed: install pymysql, mysqlclient, or mysql-connector-python"
        )

    def connect(self) -> "MysqlDriver":
        if self._conn is not None:
            return self
        mod = self._get_module()
        try:
            # MySQL drivers take individual keyword arguments, not a URL string.
            kwargs = mysql_kwargs(getattr(mod, "__name__", ""), parse_dsn(self.dsn))
            self._conn = mod.connect(**kwargs)
        except Exception as exc:
            raise DriverError(f"could not connect to MySQL: {exc}") from exc
        return self

    @property
    def conn(self) -> Any:
        if self._conn is None:
            self.connect()
        return self._conn

    def explain(self, sql: str) -> PlanEstimate:
        try:
            cur = self.conn.cursor()
            cur.execute(f"EXPLAIN FORMAT=JSON {sql}")
            row = cur.fetchone()
            cur.close()
            if not row:
                raise ExecutionError("EXPLAIN returned no rows")
            raw_json = row[0]
            if isinstance(raw_json, str):
                plan_data = json.loads(raw_json)
            else:
                plan_data = raw_json
        except Exception as exc:
            raise ExecutionError(f"could not EXPLAIN query: {exc}") from exc

        return parse_mysql_plan(plan_data)

    def execute(self, sql: str, *, max_rows: int, timeout: float) -> QueryResult:
        cur = self.conn.cursor()
        try:
            _apply_timeout(cur, timeout)
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = [list(r) for r in fetched[:max_rows]]
            return QueryResult(columns=columns, rows=rows, rowcount=len(rows), truncated=truncated)
        except Exception as exc:
            raise ExecutionError(f"MySQL query failed: {exc}") from exc
        finally:
            cur.close()

    def execute_write(self, sql: str, *, timeout: float) -> WriteResult:
        cur = self.conn.cursor()
        try:
            _apply_timeout(cur, timeout)
            cur.execute(sql)
            affected = cur.rowcount if hasattr(cur, "rowcount") else -1
            self.conn.commit()
            leading = sql.strip().split()[0].lower() if sql.strip() else ""
            return WriteResult(statement=leading, rowcount=affected)
        except Exception as exc:
            if hasattr(self.conn, "rollback"):
                self.conn.rollback()
            raise ExecutionError(f"MySQL write failed: {exc}") from exc
        finally:
            cur.close()

    def close(self) -> None:
        if self._conn is not None and hasattr(self._conn, "close"):
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
