"""Exception hierarchy."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class WebOperatorError(PolicyKitError):
    """Base class."""


class HostNotAllowed(WebOperatorError):
    """The host is not in scope (fail-closed)."""


class OperationNotAllowed(WebOperatorError):
    """The operation category is not permitted for this host."""


class MutationNotApproved(WebOperatorError):
    """A state-changing operation was not approved."""


class InvalidOperation(WebOperatorError):
    """Unknown operation name."""


class BackendError(WebOperatorError):
    """The browser backend failed (or does not support the operation)."""
