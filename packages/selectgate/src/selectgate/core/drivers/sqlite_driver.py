"""Built-in SQLite driver (stdlib). Read-only; offline-testable.

The plan gate estimate comes from ``EXPLAIN QUERY PLAN``: every full table SCAN
contributes that table's row count, each indexed SEARCH a small bounded cost.
"""

from __future__ import annotations

import re
import sqlite3
import threading

from ..errors import ExecutionError, QueryTimeout
from ..types import PlanEstimate, QueryResult, WriteResult
from . import Driver

_PLAN_RE = re.compile(r"\b(SCAN|SEARCH)\s+(?:TABLE\s+)?([A-Za-z_][A-Za-z0-9_]*)")
_SEARCH_COST = 1


class SqliteDriver(Driver):
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._conn: sqlite3.Connection | None = None
        self._rowcounts: dict[str, int] = {}

    def connect(self) -> "SqliteDriver":
        # read-only intent; check_same_thread=False lets the timeout watchdog
        # call interrupt() from another thread.
        self._conn = sqlite3.connect(self.dsn, check_same_thread=False)
        return self

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    def _table_rowcount(self, table: str) -> int:
        if table in self._rowcounts:
            return self._rowcounts[table]
        try:
            cur = self.conn.execute(f'SELECT COUNT(*) FROM "{table}"')
            n = int(cur.fetchone()[0])
        except sqlite3.Error:
            n = 0
        self._rowcounts[table] = n
        return n

    def explain(self, sql: str) -> PlanEstimate:
        try:
            cur = self.conn.execute("EXPLAIN QUERY PLAN " + sql)
            plan_rows = cur.fetchall()
        except sqlite3.Error as exc:
            raise ExecutionError(f"could not EXPLAIN query: {exc}") from None
        plan: list[str] = []
        full_scans: list[str] = []
        est = 0
        for row in plan_rows:
            detail = str(row[-1])
            plan.append(detail)
            m = _PLAN_RE.search(detail)
            if not m:
                continue
            kind, table = m.group(1), m.group(2)
            if kind == "SCAN":
                full_scans.append(table)
                est += self._table_rowcount(table)
            else:  # SEARCH (index)
                est += _SEARCH_COST
        return PlanEstimate(max_scanned_rows=est, full_scans=full_scans, plan=plan)

    def execute(self, sql: str, *, max_rows: int, timeout: float) -> QueryResult:
        timer = threading.Timer(timeout, self.conn.interrupt)
        timer.start()
        try:
            cur = self.conn.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                raise QueryTimeout(f"query exceeded {timeout}s and was interrupted") from None
            raise ExecutionError(str(exc)) from None
        except sqlite3.Error as exc:
            raise ExecutionError(str(exc)) from None
        finally:
            timer.cancel()
        truncated = len(fetched) > max_rows
        rows = [list(r) for r in fetched[:max_rows]]
        return QueryResult(columns=columns, rows=rows, rowcount=len(rows), truncated=truncated)

    def execute_write(self, sql: str, *, timeout: float) -> WriteResult:
        timer = threading.Timer(timeout, self.conn.interrupt)
        timer.start()
        try:
            cur = self.conn.execute(sql)
            self.conn.commit()
            rowcount = cur.rowcount
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                raise QueryTimeout(f"write exceeded {timeout}s and was interrupted") from None
            raise ExecutionError(str(exc)) from None
        except sqlite3.Error as exc:
            raise ExecutionError(str(exc)) from None
        finally:
            timer.cancel()
        return WriteResult(statement="", rowcount=rowcount if rowcount is not None else -1)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
