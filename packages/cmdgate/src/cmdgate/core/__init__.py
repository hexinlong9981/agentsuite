"""Public core API."""

from __future__ import annotations

from .config import Config, Limits, Policy, Rule, Target
from .errors import (
    CmdGateError,
    CommandTimeout,
    ExecutionError,
    PolicyDenied,
    TargetNotAllowed,
    ValidationError,
)
from .policy import evaluate
from .runner import list_allowed, run
from .types import CommandResult, Decision

__all__ = [
    "Config", "Target", "Policy", "Rule", "Limits",
    "run", "list_allowed", "evaluate",
    "CommandResult", "Decision",
    "CmdGateError", "PolicyDenied", "TargetNotAllowed",
    "ValidationError", "CommandTimeout", "ExecutionError",
]
