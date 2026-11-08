"""Metrics ingestion adapter for procflow run records.

Aggregates execution metrics, durations, failure counts, and step statistics from
run.json and state.jsonl files into structured, queryable data stores (SQLite / JSONL)
with full idempotence via SHA-256 fingerprinting.

Privacy & Security Guarantee:
Never ingests raw outputs, user inputs, prompt texts, or production payloads.
Only ingests numerical counts, duration metrics, timestamps, and status flags.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass
class RunSummaryRecord:
    run_id: str
    sop_id: str
    status: str
    started_at: str
    ended_at: str | None
    duration_sec: float
    total_steps: int
    failed_steps: int
    fingerprint: str
    ingested_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_run_fingerprint(run_dir: Path) -> str:
    """Calculate a deterministic SHA-256 fingerprint for a run directory."""
    hasher = hashlib.sha256()
    for fname in ("run.json", "state.jsonl"):
        f = run_dir / fname
        if f.is_file():
            hasher.update(f.read_bytes())
    return hasher.hexdigest()[:16]


def parse_run_record(run_dir: Path) -> RunSummaryRecord | None:
    """Extract metrics summary from a single run directory without sensitive payload data."""
    rf = run_dir / "run.json"
    if not rf.is_file():
        return None

    try:
        run_data = json.loads(rf.read_text(encoding="utf-8"))
    except Exception:
        return None

    run_id = run_data.get("run_id") or run_dir.name
    sop_id = run_data.get("sop") or run_dir.parent.name
    status = run_data.get("status", "unknown")
    started_at = run_data.get("started_at", "")
    ended_at = run_data.get("ended_at")

    duration = 0.0
    if started_at and ended_at:
        try:
            t0 = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration = max(0.0, (t1 - t0).total_seconds())
        except Exception:
            pass

    # Step event counting from state.jsonl
    sf = run_dir / "state.jsonl"
    distinct_steps: set[str] = set()
    failed_steps: set[str] = set()
    if sf.is_file():
        try:
            for line in sf.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                kind = ev.get("ev") or ev.get("event")
                step_name = ev.get("step")
                if kind in ("step_start", "step_done") and step_name:
                    distinct_steps.add(str(step_name))
                elif kind in ("step_fail", "step_error") and step_name:
                    failed_steps.add(str(step_name))
        except Exception:
            pass

    fingerprint = calculate_run_fingerprint(run_dir)
    ingested_at = datetime.now(timezone.utc).isoformat()

    return RunSummaryRecord(
        run_id=run_id,
        sop_id=sop_id,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        duration_sec=duration,
        total_steps=len(distinct_steps),
        failed_steps=len(failed_steps),
        fingerprint=fingerprint,
        ingested_at=ingested_at,
    )


class MetricsStore:
    """SQLite-backed metrics ingestion and query store."""

    def __init__(self, db_path: Path | str = ":memory:"):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_metrics (
                    run_id TEXT PRIMARY KEY,
                    sop_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    duration_sec REAL,
                    total_steps INTEGER,
                    failed_steps INTEGER,
                    fingerprint TEXT NOT NULL,
                    ingested_at TEXT NOT NULL
                )
                """
            )
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sop_id ON run_metrics(sop_id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON run_metrics(status)")

    def ingest_record(self, rec: RunSummaryRecord) -> bool:
        """Idempotently insert or update a run metric record."""
        with self.conn:
            cur = self.conn.execute(
                "SELECT fingerprint FROM run_metrics WHERE run_id = ?",
                (rec.run_id,),
            )
            row = cur.fetchone()
            if row and row[0] == rec.fingerprint:
                # Already up to date
                return False

            self.conn.execute(
                """
                INSERT OR REPLACE INTO run_metrics (
                    run_id, sop_id, status, started_at, ended_at,
                    duration_sec, total_steps, failed_steps, fingerprint, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rec.run_id,
                    rec.sop_id,
                    rec.status,
                    rec.started_at,
                    rec.ended_at,
                    rec.duration_sec,
                    rec.total_steps,
                    rec.failed_steps,
                    rec.fingerprint,
                    rec.ingested_at,
                ),
            )
            return True

    def scan_and_ingest(self, runs_root: Path | str) -> int:
        """Scan runs_root for all runs and ingest new or modified runs."""
        root = Path(runs_root).resolve()
        count = 0
        if not root.is_dir():
            return count

        for rf in root.rglob("run.json"):
            rec = parse_run_record(rf.parent)
            if rec:
                changed = self.ingest_record(rec)
                if changed:
                    count += 1

        return count

    def query_metrics(self, sop_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        """Query ingested metrics with optional filters."""
        sql = "SELECT run_id, sop_id, status, started_at, ended_at, duration_sec, total_steps, failed_steps FROM run_metrics WHERE 1=1"
        params: list[Any] = []
        if sop_id:
            sql += " AND sop_id = ?"
            params.append(sop_id)
        if status:
            sql += " AND status = ?"
            params.append(status)

        sql += " ORDER BY started_at DESC"
        cur = self.conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
