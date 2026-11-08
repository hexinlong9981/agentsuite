"""Public core API — the single place the CLI (and any MCP adapter) calls."""

from __future__ import annotations

from agent_policy_kit import AllowAll, Approval, Callback, DenyAll

from .config import Config, Limits, Target, WriteConfig
from .errors import (
    DriverError,
    ExecutionError,
    PlanGateExceeded,
    QueryTimeout,
    SelectGateError,
    StatementRejected,
    TargetNotAllowed,
    WriteNotApproved,
    WriteNotEnabled,
)
from .gateway import explain_query, run_select, run_write
from .types import PlanEstimate, QueryResult, WriteResult

__all__ = [
    "Config", "Limits", "Target", "WriteConfig",
    "run_select", "explain_query", "run_write",
    "PlanEstimate", "QueryResult", "WriteResult",
    "Approval", "DenyAll", "AllowAll", "Callback",
    "SelectGateError", "StatementRejected", "TargetNotAllowed",
    "PlanGateExceeded", "QueryTimeout", "ExecutionError", "DriverError",
    "WriteNotEnabled", "WriteNotApproved",
]
