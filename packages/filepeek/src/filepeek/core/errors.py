"""Exception hierarchy (shares the suite base)."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class FilepeekError(PolicyKitError):
    """Base class."""


class BackendError(FilepeekError):
    """The file store backend failed (or does not support the operation)."""
