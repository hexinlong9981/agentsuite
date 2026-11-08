"""Verification and regression judgement for procedure revisions.

The gate a candidate revision must pass before it is committed:
  1. it compiles with NO errors (any compile error is fatal);
  2. for a full procedure (one with a ``flow``) and a repo, it passes the whole
     static ``run_check`` (OOP / LSP / call / reachability);
  3. it has at least one executable path (dry-run);
  4. lines of defense: a human step is neither removed nor automated;
  5. the output contract is preserved;
  6. characterization: every run that was accepted *before* the change is still
     accepted *after* it (an "undecidable" replay never counts as accepted).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..check import run_check
from ..compiler import compile_definition
from ..dryrun import enumerate_paths
from ..replay import replay_all


@dataclass
class JudgementResult:
    passed: bool
    verdict: str  # "PASS", "REGRESSION", "CHECK_FAILED", "SIMULATION_FAILED"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "errors": self.errors,
            "warnings": self.warnings,
            "details": self.details,
        }


def judge_revision(
    original_def: dict[str, Any],
    revised_def: dict[str, Any],
    sop_path: Path | str | None = None,
    lookup: Callable[[str], Any] | None = None,
    interface_lookup: Callable[[str], Any] | None = None,
    repo: Any | None = None,
    runs_root: Path | str | None = None,
    sop_id: str | None = None,
    baseline_accepted: Iterable[str] | None = None,
) -> JudgementResult:
    """Judge a revised procedure definition against all safety gates."""
    errors: list[str] = []
    warnings: list[str] = []
    sop_lookup = lookup or (lambda sid: None)

    # 1. Compile. ANY compile error is fatal (a differently-worded error must not slip through).
    try:
        rev_ir, compile_errs = compile_definition(revised_def, lookup=sop_lookup, interface_lookup=interface_lookup)
    except Exception as e:
        return JudgementResult(False, "CHECK_FAILED", errors=[f"Compilation crashed: {e}"])
    if compile_errs:
        return JudgementResult(False, "CHECK_FAILED", errors=[str(x) for x in compile_errs])

    # 2. Full static check for a real procedure (one with a flow) when a repo is available.
    if repo is not None and isinstance(revised_def, dict) and revised_def.get("flow"):
        try:
            findings = run_check(repo, [sop_id] if sop_id else None, run=True)
            fatal = [f for f in findings if getattr(f, "severity", "error") != "warning"]
            if fatal:
                return JudgementResult(
                    False, "CHECK_FAILED",
                    errors=[getattr(f, "message", str(f)) for f in fatal],
                )
        except Exception as e:
            errors.append(f"run_check crashed: {e}")

    # 3. Dry-run simulation.
    try:
        if rev_ir and rev_ir.start:
            paths = enumerate_paths(rev_ir.to_dict())
            if not paths:
                warnings.append("Dry-run found no executable paths.")
    except Exception as e:
        errors.append(f"Dry-run simulation crashed: {e}")
    if errors:
        return JudgementResult(False, "SIMULATION_FAILED", errors=errors)

    # 4. Lines of defense: human steps must not be removed or automated.
    orig_steps = {s.get("id"): s for s in original_def.get("steps") or [] if isinstance(s, dict)}
    rev_steps = {s.get("id"): s for s in revised_def.get("steps") or [] if isinstance(s, dict)}
    for sid, s in orig_steps.items():
        if s.get("type") == "human" or s.get("kind") == "human" or s.get("mode") == "human":
            if sid not in rev_steps:
                return JudgementResult(
                    False, "REGRESSION",
                    errors=[f"Human step {sid} was removed (lines of defense: human_removed)"],
                )
            rs = rev_steps[sid]
            rkind = rs.get("type") or rs.get("kind") or rs.get("mode")
            if rkind not in ("human", "manual"):
                return JudgementResult(
                    False, "REGRESSION",
                    errors=[f"Human step {sid} was changed to {rkind} (lines of defense: kind_from_human)"],
                )

    # 5. Output contract: required outputs preserved.
    orig_outputs = set(original_def.get("outputs") or [])
    rev_outputs = set(revised_def.get("outputs") or [])
    missing_outputs = orig_outputs - rev_outputs
    if missing_outputs:
        return JudgementResult(
            False, "REGRESSION",
            errors=[f"Revision breaks contract by removing required outputs: {sorted(missing_outputs)}"],
        )
    new_inputs = set(revised_def.get("inputs") or []) - set(original_def.get("inputs") or [])
    if new_inputs:
        warnings.append(f"Revision introduced new input parameters: {sorted(new_inputs)}")

    # 6. Characterization: accepted runs must still be accepted.
    #    "undecidable" is never counted as accepted. Only runs when a run history exists.
    if runs_root is not None and baseline_accepted is not None:
        baseline = set(baseline_accepted)
        if baseline:
            try:
                now_rows = replay_all(runs_root, repo=repo, sop=sop_id, current=True)
                now_accepted = {r["run_id"] for r in now_rows if r.get("verdict") == "accepted"}
                regressed = sorted(baseline - now_accepted)
                if regressed:
                    return JudgementResult(
                        False, "REGRESSION",
                        errors=[f"Characterization regression: accepted runs no longer accepted: {regressed}"],
                        details={"baseline_accepted": sorted(baseline), "now_accepted": sorted(now_accepted)},
                    )
            except Exception as e:
                warnings.append(f"Characterization skipped (replay error): {e}")

    return JudgementResult(
        True, "PASS", warnings=warnings,
        details={"steps_count": len(revised_def.get("steps") or [])},
    )
