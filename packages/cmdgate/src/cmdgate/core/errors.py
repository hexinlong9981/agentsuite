"""Exception hierarchy."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class CmdGateError(PolicyKitError):
    """Base class."""


class PolicyDenied(CmdGateError):
    """No policy/rule allowed the command (fail-closed). Carries the reason."""


class TargetNotAllowed(CmdGateError):
    """The target is not in the configured allowlist."""


class ValidationError(CmdGateError):
    """The command argv is malformed or unsafe."""


class CommandTimeout(CmdGateError):
    """Execution exceeded the configured timeout."""


class ExecutionError(CmdGateError):
    """The command could not be launched."""
