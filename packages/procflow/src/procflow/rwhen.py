"""Resume conditions evaluation predicates for suspended procflow runs.

Provides deterministic, side-effect-free predicates to evaluate whether a suspended
or sleeping procedure run meets its requirements to resume execution.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_ts(ts_str: str | None) -> datetime | None:
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


@dataclass
class ResumeContext:
    run_id: str
    suspended_at: str | None
    attempts: int = 0
    state_variables: dict[str, Any] = field(default_factory=dict)
    workspace_root: Path | str | None = None
    approval_decision: str | None = None  # "approved", "rejected", None


def eval_exists(path_str: str, context: ResumeContext) -> bool:
    """Check if file exists under workspace_root safely."""
    p = Path(path_str)
    if not p.is_absolute() and context.workspace_root:
        p = Path(context.workspace_root) / p
    return p.exists()


def eval_file_not_empty(path_str: str, context: ResumeContext) -> bool:
    """Check if file exists and has size > 0."""
    p = Path(path_str)
    if not p.is_absolute() and context.workspace_root:
        p = Path(context.workspace_root) / p
    return p.is_file() and p.stat().st_size > 0


def eval_time_elapsed(seconds: float, context: ResumeContext) -> bool:
    """Check if elapsed seconds since suspended_at exceeds threshold."""
    if not context.suspended_at:
        return True
    dt = _parse_ts(context.suspended_at)
    if not dt:
        return True
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    elapsed = (now - dt).total_seconds()
    return elapsed >= seconds


def eval_backoff(
    base_sec: float,
    factor: float,
    max_sec: float,
    context: ResumeContext,
) -> bool:
    """Check if backoff duration elapsed for the current retry attempt count."""
    wait_time = min(max_sec, base_sec * (factor ** max(0, context.attempts - 1)))
    return eval_time_elapsed(wait_time, context)


def eval_resume_predicate(
    predicate: dict[str, Any] | str,
    context: ResumeContext,
) -> bool:
    """Evaluate a structured resume condition predicate dictionary or string rule.

    Example dict predicates:
      {"exists": "data/input.csv"}
      {"time_elapsed": 60}
      {"backoff": {"base_sec": 5, "factor": 2, "max_sec": 300}}
      {"approval": "approved"}
      {"var_eq": {"key": "ready", "val": True}}
      {"and": [...]}
      {"or": [...]}
    """
    if isinstance(predicate, str):
        rule = predicate.strip()
        if rule == "always":
            return True
        if rule.startswith("time_elapsed:"):
            sec = float(rule.split(":", 1)[1])
            return eval_time_elapsed(sec, context)
        if rule.startswith("exists:"):
            path = rule.split(":", 1)[1].strip()
            return eval_exists(path, context)
        if rule == "approved":
            return context.approval_decision == "approved"
        return False

    if not isinstance(predicate, dict):
        return False

    if "and" in predicate:
        return all(eval_resume_predicate(sub, context) for sub in predicate["and"])
    if "or" in predicate:
        return any(eval_resume_predicate(sub, context) for sub in predicate["or"])
    if "not" in predicate:
        return not eval_resume_predicate(predicate["not"], context)

    if "exists" in predicate:
        return eval_exists(str(predicate["exists"]), context)
    if "file_not_empty" in predicate:
        return eval_file_not_empty(str(predicate["file_not_empty"]), context)
    if "time_elapsed" in predicate:
        return eval_time_elapsed(float(predicate["time_elapsed"]), context)
    if "backoff" in predicate:
        cfg = predicate["backoff"]
        return eval_backoff(
            base_sec=float(cfg.get("base_sec", 10.0)),
            factor=float(cfg.get("factor", 2.0)),
            max_sec=float(cfg.get("max_sec", 3600.0)),
            context=context,
        )
    if "approval" in predicate:
        return context.approval_decision == str(predicate["approval"])
    if "var_eq" in predicate:
        cfg = predicate["var_eq"]
        k = cfg.get("key")
        expected = cfg.get("val")
        return context.state_variables.get(k) == expected

    return False
