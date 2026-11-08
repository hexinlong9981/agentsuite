"""The human-decision source for ``human`` steps (and any approval-gated action).

A thin adapter over :class:`agent_policy_kit.Approval`. The engine never decides for a human: a
``submit --decision <d>`` is only a *claim*; it is recorded only when the approval source confirms it
(``request(target="<run>/<step>", operation="decision:<d>", preview=...)`` returns True). The default
source is :class:`~agent_policy_kit.DenyAll`, so nothing is ever self-approved.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_policy_kit import AllowAll, Approval, Callback, DenyAll  # noqa: F401  (re-exported)

from .errors import ProcflowError


class DecisionRefused(ProcflowError):
    """The approval source did not confirm the claimed decision."""


class ScriptedApproval(Approval):
    """A fake / scripted source: ``{step_id: decision}``. Confirms only the scripted decision for a step."""

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.asked = []

    def request(self, target, operation, preview):
        step = target.rsplit("/", 1)[-1]
        decision = operation.split(":", 1)[1] if ":" in operation else operation
        self.asked.append((target, operation))
        return self.script.get(step) == decision


def file_approval(path):
    """Scripted source from a JSON file ``{"<step>": "<decision>"}`` (CI / hand-prepared decisions)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(d, dict):
        raise ValueError("the approval file must be a JSON map {step: decision}")
    return ScriptedApproval({str(k): str(v) for k, v in d.items()})


def prompt_approval(input_fn=input, out=print):
    """Interactive source: a person answers y/N at a terminal."""
    def ask(target, operation, preview):
        out(f"{target}: {operation}\n{preview}")
        return str(input_fn("confirm? [y/N] ")).strip().lower() in ("y", "yes")
    return Callback(ask)


def get_approval(spec):
    """None/"deny" -> DenyAll; "prompt"; "file:<path>". ("allow" is for tests only and not offered here.)"""
    if spec in (None, "", "deny"):
        return DenyAll()
    if spec == "prompt":
        return prompt_approval()
    if isinstance(spec, str) and spec.startswith("file:") and len(spec) > 5:
        return file_approval(spec[5:])
    raise ValueError(f"unknown approval source {spec!r} (deny | prompt | file:<path>)")


class ApprovalSource:
    """What the engine holds: confirms a claimed decision through the injected Approval."""

    def __init__(self, approval=None):
        self.approval = approval or DenyAll()

    def confirm(self, run_id, step, decision, preview=""):
        return bool(self.approval.request(f"{run_id}/{step}", f"decision:{decision}", preview))
