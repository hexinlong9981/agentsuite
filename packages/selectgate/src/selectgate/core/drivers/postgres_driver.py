"""PostgreSQL driver with plan-gate support via EXPLAIN (FORMAT JSON).

Read queries are constrained to be read-only *upstream* by the gateway's
statement guard (SELECT-only parse); this driver adds a per-read statement
timeout. Write-mode is a separate, explicitly-enabled path (``execute_write``).
Supports psycopg / psycopg2 / pg8000 when installed, and an offline-testable
mock connection mode for CI/tests.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

from ..errors import DriverError, ExecutionError
from ..types import PlanEstimate, QueryResult, WriteResult
from . import Driver
from .dsn import parse_dsn, postgres_kwargs


def parse_postgres_plan(plan_data: list[dict[str, Any]]) -> PlanEstimate:
    """Parse PostgreSQL EXPLAIN (FORMAT JSON) output into a PlanEstimate."""
    if not plan_data or not isinstance(plan_data, list):
        return PlanEstimate(max_scanned_rows=0, full_scans=[], plan=[])

    full_scans: list[str] = []
    plan_lines: list[str] = []
    total_scanned_rows = 0

    def traverse(node: dict[str, Any]) -> None:
        nonlocal total_scanned_rows
        node_type = node.get("Node Type", "Unknown")
        relation = node.get("Relation Name", "")
        plan_rows = int(node.get("Plan Rows", 0))

        line_desc = f"{node_type}"
        if relation:
            line_desc += f" on {relation}"
        line_desc += f" (rows={plan_rows})"
        plan_lines.append(line_desc)

        if node_type == "Seq Scan":
            if relation:
                full_scans.append(relation)
            total_scanned_rows += plan_rows
        elif "Index" in node_type or "Bitmap" in node_type:
            total_scanned_rows += max(1, plan_rows)
        else:
            total_scanned_rows += plan_rows

        for subplan in node.get("Plans", []):
            traverse(subplan)

    root_plan = plan_data[0].get("Plan", {})
    if root_plan:
        traverse(root_plan)

    return PlanEstimate(
        max_scanned_rows=total_scanned_rows,
        full_scans=full_scans,
        plan=plan_lines,
    )


class PostgresDriver(Driver):
    """PostgreSQL driver adapter."""

    def __init__(self, dsn: str, *, raw_conn: Any = None) -> None:
        super().__init__(dsn)
        self._conn = raw_conn
        self._driver_module: Any = None

    def _get_module(self) -> Any:
        if self._driver_module is not None:
            return self._driver_module
        for mod_name in ("psycopg", "psycopg2", "pg8000"):
            try:
                mod = importlib.import_module(mod_name)
                self._driver_module = mod
                return mod
            except ImportError:
                continue
        raise DriverError(
            "no PostgreSQL driver installed: install psycopg, psycopg2, or pg8000"
        )

    def connect(self) -> "PostgresDriver":
        if self._conn is not None:
            return self
        mod = self._get_module()
        try:
            # psycopg / psycopg2 accept a conninfo string / URL directly; pg8000
            # needs individual keyword arguments.
            if getattr(mod, "__name__", "") == "pg8000":
                self._conn = mod.connect(**postgres_kwargs(parse_dsn(self.dsn)))
            else:
                self._conn = mod.connect(self.dsn)
        except Exception as exc:
            raise DriverError(f"could not connect to PostgreSQL: {exc}") from exc
        return self

    @property
    def conn(self) -> Any:
        if self._conn is None:
            self.connect()
        return self._conn

    def explain(self, sql: str) -> PlanEstimate:
        try:
            cur = self.conn.cursor()
            cur.execute(f"EXPLAIN (FORMAT JSON) {sql}")
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

        return parse_postgres_plan(plan_data)

    def execute(self, sql: str, *, max_rows: int, timeout: float) -> QueryResult:
        cur = self.conn.cursor()
        try:
            cur.execute(f"SET statement_timeout = {int(timeout * 1000)}")
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(max_rows + 1)
            truncated = len(fetched) > max_rows
            rows = [list(r) for r in fetched[:max_rows]]
            return QueryResult(columns=columns, rows=rows, rowcount=len(rows), truncated=truncated)
        except Exception as exc:
            raise ExecutionError(f"PostgreSQL query failed: {exc}") from exc
        finally:
            cur.close()

    def execute_write(self, sql: str, *, timeout: float) -> WriteResult:
        cur = self.conn.cursor()
        try:
            cur.execute(f"SET statement_timeout = {int(timeout * 1000)}")
            cur.execute(sql)
            affected = cur.rowcount if hasattr(cur, "rowcount") else -1
            self.conn.commit()
            leading = sql.strip().split()[0].lower() if sql.strip() else ""
            return WriteResult(statement=leading, rowcount=affected)
        except Exception as exc:
            if hasattr(self.conn, "rollback"):
                self.conn.rollback()
            raise ExecutionError(f"PostgreSQL write failed: {exc}") from exc
        finally:
            cur.close()

    def close(self) -> None:
        if self._conn is not None and hasattr(self._conn, "close"):
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
