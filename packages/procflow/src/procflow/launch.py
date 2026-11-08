"""Unified launcher and periodic scheduler tick for procflow runs.

Coordinates scheduled launches, suspended run resumption evaluations, and periodic
monitoring without external system dependencies.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import notify as NT
from . import resume_eval as RE
from . import schedule as SC


@dataclass
class TickOutcome:
    tick_id: str
    timestamp: str
    schedules_checked: int = 0
    schedules_triggered: list[str] = field(default_factory=list)
    resumes_evaluated: int = 0
    resumes_ready: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick_id": self.tick_id,
            "ts": self.timestamp,
            "schedules_checked": self.schedules_checked,
            "schedules_triggered": self.schedules_triggered,
            "resumes_evaluated": self.resumes_evaluated,
            "resumes_ready": self.resumes_ready,
            "errors": self.errors,
        }


def run_scheduler_tick(
    schedules_file: Path | str | None,
    runs_root: Path | str,
    now_dt: datetime | None = None,
) -> TickOutcome:
    """Execute a single scheduler evaluation tick: check cron jobs and suspended runs."""
    now = now_dt or datetime.now(timezone.utc)
    ts_str = now.isoformat()
    tick_id = f"tick_{now.strftime('%Y%m%d_%H%M%S')}"

    outcome = TickOutcome(tick_id=tick_id, timestamp=ts_str)
    root = Path(runs_root).resolve()

    # 1. Evaluate schedule triggers if config exists
    if schedules_file:
        sf = Path(schedules_file).resolve()
        if sf.exists():
            entries = SC.load_schedules(sf)
            outcome.schedules_checked = len(entries)
            for entry in entries:
                if not entry.enabled:
                    continue
                try:
                    cs = SC.CronSchedule.parse(entry.cron_expr)
                    if cs.matches(now):
                        outcome.schedules_triggered.append(entry.schedule_id)
                        NT.notify(
                            runs_root=root,
                            kind="schedule_triggered",
                            message=f"Schedule '{entry.schedule_id}' matched cron {entry.cron_expr}",
                            sop_id=entry.sop_id,
                            details={"cron": entry.cron_expr, "schedule_id": entry.schedule_id},
                            timestamp=ts_str,
                        )
                except Exception as e:
                    outcome.errors.append(f"Schedule '{entry.schedule_id}' failed: {e}")
                    NT.notify(
                        runs_root=root,
                        kind="schedule_error",
                        message=f"Failed to evaluate schedule '{entry.schedule_id}': {e}",
                        sop_id=entry.sop_id,
                        details={"error": str(e)},
                        timestamp=ts_str,
                    )

    # 2. Evaluate suspended runs for resumption
    try:
        resume_results = RE.scan_and_evaluate_all(runs_root=root)
        outcome.resumes_evaluated = len(resume_results)
        for r in resume_results:
            if r.can_resume:
                outcome.resumes_ready.append(r.run_id)
                NT.notify(
                    runs_root=root,
                    kind="auto_resume",
                    message=f"Run '{r.run_id}' satisfied resume condition: {r.reason}",
                    sop_id=r.sop_id,
                    run_id=r.run_id,
                    details=r.details,
                    timestamp=ts_str,
                )
    except Exception as e:
        outcome.errors.append(f"Resumption scan failed: {e}")

    # 3. Log tick outcome to _schedule/tick.jsonl
    sched_log_dir = root / "_schedule"
    sched_log_dir.mkdir(parents=True, exist_ok=True)
    tick_log = sched_log_dir / "tick.jsonl"
    try:
        with tick_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(outcome.to_dict()) + "\n")
    except Exception:
        pass

    return outcome
