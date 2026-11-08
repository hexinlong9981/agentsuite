"""Exception hierarchy."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class RemoteFileSyncError(PolicyKitError):
    """Base class."""


class PolicyNotAllowed(RemoteFileSyncError):
    """No such sync policy (fail-closed)."""


class SourceNotAllowed(RemoteFileSyncError):
    """No such source (fail-closed)."""


class DestinationError(RemoteFileSyncError):
    """Unknown category, or a path that would escape its destination root."""


class TransportError(RemoteFileSyncError):
    """The transport could not list/read (or is not implemented)."""


class LimitExceeded(RemoteFileSyncError):
    """A configured file-count or byte limit was exceeded."""
