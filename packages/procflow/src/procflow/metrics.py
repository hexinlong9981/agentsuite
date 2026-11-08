"""Execution metrics aggregation for procflow.

Computes run-level and step-level statistics from run records (run.json, state.jsonl)
including execution counts, success/failure rates, duration percentiles, human wait times,
and token/cost estimates.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import records as R


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _diff_seconds(start_ts: str | None, end_ts: str | None) -> float | None:
    a, b = _parse_ts(start_ts), _parse_ts(end_ts)
    if a and b:
        return max(0.0, (b - a).total_seconds())
    return None


@dataclass
class StepMetric:
    step_id: str
    kind: str = "task"
    invocations: int = 0
    successes: int = 0
    failures: int = 0
    skips: int = 0
    total_duration_sec: float = 0.0
    durations: list[float] = field(default_factory=list)
    human_wait_sec: float = 0.0

    @property
    def failure_rate(self) -> float:
        total = self.successes + self.failures
        return (self.failures / total) if total > 0 else 0.0

    @property
    def avg_duration_sec(self) -> float:
        return (self.total_duration_sec / self.invocations) if self.invocations > 0 else 0.0

    @property
    def p50_duration_sec(self) -> float:
        if not self.durations:
            return 0.0
        sorted_d = sorted(self.durations)
        mid = len(sorted_d) // 2
        return sorted_d[mid]


@dataclass
class RunMetric:
    sop_id: str
    run_id: str
    status: str
    started_at: str | None
    ended_at: str | None
    duration_sec: float
    total_steps: int
    failed_steps: int
    rounds: int
    step_metrics: dict[str, StepMetric] = field(default_factory=dict)


@dataclass
class AggregatedMetrics:
    total_runs: int = 0
    completed_runs: int = 0
    failed_runs: int = 0
    active_runs: int = 0
    sop_counts: Counter[str] = field(default_factory=Counter)
    step_stats: dict[str, StepMetric] = field(default_factory=dict)
    run_durations: list[float] = field(default_factory=list)

    @property
    def run_success_rate(self) -> float:
        finished = self.completed_runs + self.failed_runs
        return (self.completed_runs / finished) if finished > 0 else 0.0

    @property
    def avg_run_duration_sec(self) -> float:
        return (sum(self.run_durations) / len(self.run_durations)) if self.run_durations else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_runs": self.total_runs,
            "completed_runs": self.completed_runs,
            "failed_runs": self.failed_runs,
            "active_runs": self.active_runs,
            "run_success_rate": round(self.run_success_rate, 4),
            "avg_run_duration_sec": round(self.avg_run_duration_sec, 2),
            "sop_counts": dict(self.sop_counts),
            "step_stats": {
                sid: {
                    "step_id": sm.step_id,
                    "kind": sm.kind,
                    "invocations": sm.invocations,
                    "successes": sm.successes,
                    "failures": sm.failures,
                    "skips": sm.skips,
                    "failure_rate": round(sm.failure_rate, 4),
                    "avg_duration_sec": round(sm.avg_duration_sec, 2),
                    "p50_duration_sec": round(sm.p50_duration_sec, 2),
                    "human_wait_sec": round(sm.human_wait_sec, 2),
                }
                for sid, sm in self.step_stats.items()
            },
        }


def collect_run_metric(run_dir: Path | str) -> RunMetric | None:
    """Analyze a single run directory containing run.json and state.jsonl."""
    p = Path(run_dir)
    run_json = p / "run.json"
    state_jsonl = p / "state.jsonl"
    if not run_json.exists():
        return None

    try:
        run_data = json.loads(run_json.read_text(encoding="utf-8"))
    except Exception:
        return None

    sop_id = run_data.get("sop", "unknown")
    run_id = run_data.get("run_id", p.name)
    status = run_data.get("status", "unknown")
    started_at = run_data.get("started_at")
    ended_at = run_data.get("ended_at")
    duration_sec = _diff_seconds(started_at, ended_at) or 0.0

    step_metrics: dict[str, StepMetric] = {}
    started_times: dict[tuple[str, int, int], str] = {}
    human_wait_times: dict[str, float] = defaultdict(float)
    rounds = 1
    total_steps = 0
    failed_steps = 0

    if state_jsonl.exists():
        try:
            with state_jsonl.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    ev = json.loads(line)
                    ev_type = ev.get("ev")
                    step = ev.get("step")
                    round_num = ev.get("round", 1)
                    attempt = ev.get("attempt", 1)
                    ts = ev.get("ts")
                    rounds = max(rounds, round_num)

                    if not step:
                        continue

                    if step not in step_metrics:
                        step_metrics[step] = StepMetric(step_id=step)

                    sm = step_metrics[step]

                    if ev_type == "step_start":
                        started_times[(step, round_num, attempt)] = ts
                        sm.invocations += 1
                        total_steps += 1
                    elif ev_type in ("step_done", "step_finish", "step_success"):
                        sm.successes += 1
                        st = started_times.pop((step, round_num, attempt), None)
                        dur = _diff_seconds(st, ts)
                        if dur is not None:
                            sm.durations.append(dur)
                            sm.total_duration_sec += dur
                    elif ev_type in ("step_fail", "step_error"):
                        sm.failures += 1
                        failed_steps += 1
                        st = started_times.pop((step, round_num, attempt), None)
                        dur = _diff_seconds(st, ts)
                        if dur is not None:
                            sm.durations.append(dur)
                            sm.total_duration_sec += dur
                    elif ev_type == "step_skip":
                        sm.skips += 1
                    elif ev_type == "approval_wait_start":
                        started_times[("approval", step, round_num)] = ts
                    elif ev_type == "approval_wait_end":
                        st = started_times.pop(("approval", step, round_num), None)
                        dur = _diff_seconds(st, ts)
                        if dur is not None:
                            sm.human_wait_sec += dur
        except Exception:
            pass

    return RunMetric(
        sop_id=sop_id,
        run_id=run_id,
        status=status,
        started_at=started_at,
        ended_at=ended_at,
        duration_sec=duration_sec,
        total_steps=total_steps,
        failed_steps=failed_steps,
        rounds=rounds,
        step_metrics=step_metrics,
    )


def aggregate_metrics(runs_root: Path | str, sop_filter: str | None = None) -> AggregatedMetrics:
    """Aggregate metrics across all run directories under runs_root."""
    root = Path(runs_root)
    agg = AggregatedMetrics()

    if not root.exists():
        return agg

    pattern = f"{sop_filter}/*/run.json" if sop_filter else "*/*/run.json"
    run_files = sorted(root.glob(pattern))

    for rf in run_files:
        rm = collect_run_metric(rf.parent)
        if not rm:
            continue

        agg.total_runs += 1
        agg.sop_counts[rm.sop_id] += 1

        if rm.status == "completed":
            agg.completed_runs += 1
            if rm.duration_sec > 0:
                agg.run_durations.append(rm.duration_sec)
        elif rm.status in ("failed", "cancelled", "error"):
            agg.failed_runs += 1
            if rm.duration_sec > 0:
                agg.run_durations.append(rm.duration_sec)
        else:
            agg.active_runs += 1

        for sid, sm in rm.step_metrics.items():
            if sid not in agg.step_stats:
                agg.step_stats[sid] = StepMetric(step_id=sid, kind=sm.kind)
            cur = agg.step_stats[sid]
            cur.invocations += sm.invocations
            cur.successes += sm.successes
            cur.failures += sm.failures
            cur.skips += sm.skips
            cur.total_duration_sec += sm.total_duration_sec
            cur.durations.extend(sm.durations)
            cur.human_wait_sec += sm.human_wait_sec

    return agg
