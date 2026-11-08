"""Refactoring proposal engine for procflow SOPs.

Transforms detected smell findings into actionable, structured refactoring proposals
and YAML AST/dictionary patches.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .smells import SmellFinding


@dataclass
class RefactorProposal:
    proposal_id: str
    sop_id: str
    target_step_id: str | None
    refactoring_type: str
    title: str
    rationale: str
    patch_diff: dict[str, Any] = field(default_factory=dict)
    proposed_sop_yaml: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "sop_id": self.sop_id,
            "target_step_id": self.target_step_id,
            "refactoring_type": self.refactoring_type,
            "title": self.title,
            "rationale": self.rationale,
            "patch_diff": self.patch_diff,
        }


def generate_refactor_proposals(
    sop_def: dict[str, Any],
    findings: list[SmellFinding],
) -> list[RefactorProposal]:
    """Generate structured refactoring proposals based on detected smells."""
    proposals: list[RefactorProposal] = []
    sop_id = sop_def.get("id") or "unknown"
    idx = 1

    for f in findings:
        pid = f"PROP-{idx:03d}"
        idx += 1

        if f.smell_id == "LONG_DURATION_STEP" and f.step_id:
            # Propose introducing explicit timeout
            modified = copy.deepcopy(sop_def)
            steps = modified.get("steps") or []
            patch = {"timeout_sec": 300, "retry": {"max_attempts": 2, "backoff_sec": 5}}

            for step in steps:
                if step.get("id") == f.step_id:
                    step.update(patch)
                    break

            proposals.append(
                RefactorProposal(
                    proposal_id=pid,
                    sop_id=sop_id,
                    target_step_id=f.step_id,
                    refactoring_type="INTRODUCE_TIMEOUT",
                    title=f"Add timeout and backoff retry to step '{f.step_id}'",
                    rationale=f.message,
                    patch_diff={"step_id": f.step_id, "additions": patch},
                    proposed_sop_yaml=modified,
                )
            )

        elif f.smell_id == "HIGH_FAILURE_RATE" and f.step_id:
            # Propose introducing assertion/precondition check before the step
            precond_step_id = f"assert_preconditions_for_{f.step_id}"
            modified = copy.deepcopy(sop_def)
            steps = modified.get("steps") or []
            new_steps = []

            for step in steps:
                if step.get("id") == f.step_id:
                    new_steps.append({
                        "id": precond_step_id,
                        "name": f"Assert Preconditions for {f.step_id}",
                        "type": "code",
                        "action": "Verify required resources, permissions, and inputs before proceeding.",
                    })
                new_steps.append(step)

            modified["steps"] = new_steps

            proposals.append(
                RefactorProposal(
                    proposal_id=pid,
                    sop_id=sop_id,
                    target_step_id=f.step_id,
                    refactoring_type="ADD_PRECONDITION",
                    title=f"Introduce precondition check before unstable step '{f.step_id}'",
                    rationale=f"Step has a high failure rate ({f.metrics_evidence.get('failure_rate', 0):.1%}). Pre-validation prevents cascading errors.",
                    patch_diff={"insert_before": f.step_id, "new_step_id": precond_step_id},
                    proposed_sop_yaml=modified,
                )
            )

        elif f.smell_id == "LARGE_SOP":
            # Propose decomposing SOP
            proposals.append(
                RefactorProposal(
                    proposal_id=pid,
                    sop_id=sop_id,
                    target_step_id=None,
                    refactoring_type="EXTRACT_SOP",
                    title=f"Decompose oversized SOP '{sop_id}' into sub-procedures",
                    rationale=f"SOP contains {f.metrics_evidence.get('task_count')} tasks. Split into logical modules invoked via child runs (type: call).",
                    patch_diff={"strategy": "extract_sub_sops"},
                    proposed_sop_yaml=None,
                )
            )

        elif f.smell_id == "HUMAN_APPROVAL_BOTTLENECK" and f.step_id:
            proposals.append(
                RefactorProposal(
                    proposal_id=pid,
                    sop_id=sop_id,
                    target_step_id=f.step_id,
                    refactoring_type="SIMPLIFY_APPROVAL",
                    title=f"Streamline human approval at '{f.step_id}'",
                    rationale=f.message,
                    patch_diff={"step_id": f.step_id, "action": "batch_or_auto_verify"},
                    proposed_sop_yaml=None,
                )
            )

    return proposals
