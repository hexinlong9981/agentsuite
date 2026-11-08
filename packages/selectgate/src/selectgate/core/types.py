from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class PlanEstimate:
    max_scanned_rows: int
    full_scans: list[str] = field(default_factory=list)   # tables scanned fully
    plan: list[str] = field(default_factory=list)         # raw EXPLAIN plan lines


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list]
    rowcount: int
    truncated: bool = False


@dataclass(frozen=True)
class WriteResult:
    statement: str          # the leading keyword (insert/update/delete/…)
    rowcount: int           # rows affected (-1 if the driver cannot report it)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
