"""Exception hierarchy (shares the suite base)."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class MailpeekError(PolicyKitError):
    """Base class."""


class BackendError(MailpeekError):
    """The mail backend failed (or does not support the operation)."""
