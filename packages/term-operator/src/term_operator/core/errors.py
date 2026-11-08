"""Exception hierarchy (shares the suite base)."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class TermOperatorError(PolicyKitError):
    """Base class."""


class SessionNotAllowed(TermOperatorError):
    """The session is not in the configured allowlist (fail-closed)."""


class OperationNotAllowed(TermOperatorError):
    """The operation is not permitted for this session."""


class CommandNotAllowed(TermOperatorError):
    """The command to send did not match the send-command allowlist."""


class SendNotApproved(TermOperatorError):
    """A send (write) was not approved."""


class BackendError(TermOperatorError):
    """The terminal backend failed (or does not support the operation)."""
