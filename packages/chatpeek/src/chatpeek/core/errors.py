"""Exception hierarchy (shares the suite base)."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class ChatpeekError(PolicyKitError):
    """Base class."""


class BackendError(ChatpeekError):
    """The chat backend failed (or does not support the operation)."""
