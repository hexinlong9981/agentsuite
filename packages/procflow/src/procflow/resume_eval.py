"""Evaluation of resume conditions for suspended procflow runs.

Scans suspended run records, evaluates deterministic resume conditions via rwhen,
and appends structured evaluation logs to resume_eval.jsonl.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import rwhen


@dataclass
class ResumeEvaluationResult:
    run_id: str
    sop_id: str
    can_resume: bool
    reason: str
    evaluated_at: str
    condition: dict[str, Any] | str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "sop_id": self.sop_id,
            "can_resume": self.can_resume,
            "reason": self.reason,
            "evaluated_at": self.evaluated_at,
            "condition": self.condition,
            "details": self.details,
        }


def evaluate_run_resumption(
    run_dir: Path | str,
    workspace_root: Path | str | None = None,
) -> ResumeEvaluationResult | None:
    """Evaluate whether a specific suspended run directory meets its resumption conditions."""
    rd = Path(run_dir).resolve()
    run_json = rd / "run.json"
    if not run_json.exists():
        return None

    try:
        run_data = json.loads(run_json.read_text(encoding="utf-8"))
    except Exception:
        return None

    status = run_data.get("status")
    run_id = run_data.get("run_id", rd.name)
    sop_id = run_data.get("sop", "unknown")

    now_iso = datetime.now(timezone.utc).isoformat()

    if status != "suspended":
        return ResumeEvaluationResult(
            run_id=run_id,
            sop_id=sop_id,
            can_resume=False,
            reason=f"Run is not suspended (status={status})",
            evaluated_at=now_iso,
        )

    # Load resume_when condition from run_data or dedicated resume_when.json
    condition = run_data.get("resume_when")
    rw_file = rd / "resume_when.json"
    if not condition and rw_file.exists():
        try:
            condition = json.loads(rw_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not condition:
        return ResumeEvaluationResult(
            run_id=run_id,
            sop_id=sop_id,
            can_resume=False,
            reason="No resume_when condition declared",
            evaluated_at=now_iso,
        )

    # Build resume context
    suspended_at = run_data.get("suspended_at") or run_data.get("updated_at")
    attempts = run_data.get("resumes_count", 0)
    state_vars = run_data.get("variables", {})

    # Check for approval state in approval.json if present
    approval_file = rd / "approval.json"
    decision = None
    if approval_file.exists():
        try:
            app_data = json.loads(approval_file.read_text(encoding="utf-8"))
            decision = app_data.get("decision")
        except Exception:
            pass

    ctx = rwhen.ResumeContext(
        run_id=run_id,
        suspended_at=suspended_at,
        attempts=attempts,
        state_variables=state_vars,
        workspace_root=workspace_root or rd.parent.parent,
        approval_decision=decision,
    )

    met = rwhen.eval_resume_predicate(condition, ctx)
    res = ResumeEvaluationResult(
        run_id=run_id,
        sop_id=sop_id,
        can_resume=met,
        reason="Condition satisfied" if met else "Condition not yet satisfied",
        evaluated_at=now_iso,
        condition=condition,
        details={"attempts": attempts, "suspended_at": suspended_at},
    )

    # Append evaluation record
    eval_log = rd / "resume_eval.jsonl"
    try:
        with eval_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(res.to_dict()) + "\n")
    except Exception:
        pass

    return res


def scan_and_evaluate_all(
    runs_root: Path | str,
    workspace_root: Path | str | None = None,
) -> list[ResumeEvaluationResult]:
    """Scan all runs under runs_root and evaluate any suspended runs."""
    root = Path(runs_root).resolve()
    results: list[ResumeEvaluationResult] = []

    if not root.exists():
        return results

    for rjson in root.glob("*/*/run.json"):
        res = evaluate_run_resumption(rjson.parent, workspace_root=workspace_root)
        if res and res.condition:
            results.append(res)

    return results
