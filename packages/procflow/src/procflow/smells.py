"""Process and Code Smell detection for procflow SOPs.

Analyzes SOP definitions (IR) and execution metrics to detect architectural and operational
smells, pairing each finding with suggested refactoring patterns.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import metrics as M


DEFAULT_THRESHOLDS = {
    "long_action_chars": 150,
    "long_step_seconds": 3600.0,
    "large_sop_tasks": 15,
    "high_failure_rate": 0.20,
    "excessive_retries": 3,
    "human_bottleneck_ratio": 0.50,
    "dead_min_runs": 10,
    "complex_branch_exits": 5,
}

REFACTORING_PATTERNS = {
    "EXTRACT_SOP": "Extract Sub-SOP (Decompose monolithic SOP into modular child SOPs)",
    "REPLACE_WITH_CODE": "Replace LLM/Agent with Deterministic Code",
    "INTRODUCE_TIMEOUT": "Introduce Explicit Timeout and Retry Backoff",
    "SIMPLIFY_APPROVAL": "Batch or Streamline Human Approvals",
    "REMOVE_DEAD_STEP": "Remove Unreachable or Unused Step",
    "SPLIT_STEP": "Split Oversized Step into Smaller Atomic Actions",
    "ADD_PRECONDITION": "Introduce Input Assertion / Precondition Check",
}


@dataclass
class SmellFinding:
    smell_id: str
    sop_id: str
    step_id: str | None
    severity: str  # "high", "medium", "low"
    message: str
    metrics_evidence: dict[str, Any] = field(default_factory=dict)
    recommended_refactoring: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "smell_id": self.smell_id,
            "sop_id": self.sop_id,
            "step_id": self.step_id,
            "severity": self.severity,
            "message": self.message,
            "metrics_evidence": self.metrics_evidence,
            "recommended_refactoring": self.recommended_refactoring,
        }


def detect_static_smells(ir: dict[str, Any], thresholds: dict[str, Any] | None = None) -> list[SmellFinding]:
    """Detect architectural smells solely from SOP IR structure."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    findings: list[SmellFinding] = []
    sop_id = ir.get("id") or ir.get("sop") or "unknown"
    nodes = ir.get("nodes") or []

    # 1. Large SOP (too many task nodes)
    tasks = [n for n in nodes if n.get("type") in ("task", "step")]
    if len(tasks) > th["large_sop_tasks"]:
        findings.append(
            SmellFinding(
                smell_id="LARGE_SOP",
                sop_id=sop_id,
                step_id=None,
                severity="medium",
                message=f"SOP contains {len(tasks)} tasks, exceeding threshold of {th['large_sop_tasks']}.",
                metrics_evidence={"task_count": len(tasks), "threshold": th["large_sop_tasks"]},
                recommended_refactoring=REFACTORING_PATTERNS["EXTRACT_SOP"],
            )
        )

    # 2. Per-step static smells
    for node in nodes:
        step_id = node.get("id")
        action = str(node.get("action") or node.get("prompt") or "")
        if len(action) > th["long_action_chars"]:
            findings.append(
                SmellFinding(
                    smell_id="LONG_ACTION_TEXT",
                    sop_id=sop_id,
                    step_id=step_id,
                    severity="low",
                    message=f"Step '{step_id}' action description has {len(action)} characters, which may hide multiple responsibilities.",
                    metrics_evidence={"action_chars": len(action), "threshold": th["long_action_chars"]},
                    recommended_refactoring=REFACTORING_PATTERNS["SPLIT_STEP"],
                )
            )

        # Check for complex branching
        choices = node.get("choices") or node.get("branches") or []
        if len(choices) > th["complex_branch_exits"]:
            findings.append(
                SmellFinding(
                    smell_id="COMPLEX_BRANCHING",
                    sop_id=sop_id,
                    step_id=step_id,
                    severity="medium",
                    message=f"Step '{step_id}' has {len(choices)} outbound branches, indicating high cognitive complexity.",
                    metrics_evidence={"branch_count": len(choices), "threshold": th["complex_branch_exits"]},
                    recommended_refactoring=REFACTORING_PATTERNS["EXTRACT_SOP"],
                )
            )

    return findings


def detect_runtime_smells(
    sop_id: str,
    agg_metrics: M.AggregatedMetrics,
    thresholds: dict[str, Any] | None = None,
) -> list[SmellFinding]:
    """Detect process smells by analyzing aggregated runtime metrics."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    findings: list[SmellFinding] = []

    for sid, sm in agg_metrics.step_stats.items():
        if sm.invocations == 0:
            continue

        # 1. High failure rate
        if sm.invocations >= 3 and sm.failure_rate >= th["high_failure_rate"]:
            findings.append(
                SmellFinding(
                    smell_id="HIGH_FAILURE_RATE",
                    sop_id=sop_id,
                    step_id=sid,
                    severity="high",
                    message=f"Step '{sid}' has a failure rate of {sm.failure_rate:.1%} across {sm.invocations} runs.",
                    metrics_evidence={"failure_rate": sm.failure_rate, "invocations": sm.invocations},
                    recommended_refactoring=REFACTORING_PATTERNS["ADD_PRECONDITION"],
                )
            )

        # 2. Long step duration (p50)
        if sm.p50_duration_sec >= th["long_step_seconds"]:
            findings.append(
                SmellFinding(
                    smell_id="LONG_DURATION_STEP",
                    sop_id=sop_id,
                    step_id=sid,
                    severity="medium",
                    message=f"Step '{sid}' median duration is {sm.p50_duration_sec:.1f}s, exceeding {th['long_step_seconds']}s.",
                    metrics_evidence={"p50_duration_sec": sm.p50_duration_sec},
                    recommended_refactoring=REFACTORING_PATTERNS["INTRODUCE_TIMEOUT"],
                )
            )

        # 3. Human bottleneck (wait time is dominant)
        if sm.total_duration_sec > 0 and (sm.human_wait_sec / sm.total_duration_sec) >= th["human_bottleneck_ratio"]:
            findings.append(
                SmellFinding(
                    smell_id="HUMAN_APPROVAL_BOTTLENECK",
                    sop_id=sop_id,
                    step_id=sid,
                    severity="medium",
                    message=f"Step '{sid}' spends {sm.human_wait_sec / sm.total_duration_sec:.1%} of its time awaiting human approval.",
                    metrics_evidence={"human_wait_sec": sm.human_wait_sec, "total_sec": sm.total_duration_sec},
                    recommended_refactoring=REFACTORING_PATTERNS["SIMPLIFY_APPROVAL"],
                )
            )

    return findings


def detect_all_smells(
    ir: dict[str, Any],
    agg_metrics: M.AggregatedMetrics | None = None,
    thresholds: dict[str, Any] | None = None,
) -> list[SmellFinding]:
    """Run both static IR checks and runtime metric analysis."""
    findings = detect_static_smells(ir, thresholds=thresholds)
    if agg_metrics:
        sop_id = ir.get("id") or ir.get("sop") or "unknown"
        findings.extend(detect_runtime_smells(sop_id, agg_metrics, thresholds=thresholds))
    return findings
