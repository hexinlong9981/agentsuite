"""Exception hierarchy. No exception carries query values or credentials."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class SelectGateError(PolicyKitError):
    """Base class."""


class StatementRejected(SelectGateError):
    """The statement is not a single read-only statement (fail-closed)."""


class TargetNotAllowed(SelectGateError):
    """The target is not in the configured allowlist (fail-closed)."""


class PlanGateExceeded(SelectGateError):
    """EXPLAIN estimates the query would scan more than the allowed budget."""


class QueryTimeout(SelectGateError):
    """Execution exceeded the configured timeout and was interrupted."""


class ExecutionError(SelectGateError):
    """The driver failed to execute the (already-validated) query."""


class DriverError(SelectGateError):
    """No such driver / driver could not connect."""


class WriteNotEnabled(SelectGateError):
    """A write was attempted but write-mode is not enabled for this gateway."""


class WriteNotApproved(SelectGateError):
    """A write was allowed by the statement allowlist but not approved."""
