"""Exception hierarchy. No exception ever carries a secret value."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class GitborrowError(PolicyKitError):
    """Base class for all gitborrow errors."""


class ContainmentError(GitborrowError):
    """A target or ephemeral root resolved to a protected/invalid location."""


class GuardViolation(GitborrowError):
    """A git command/option was not allowed (push guard or option injection)."""


class RepositoryNotAllowed(GitborrowError):
    """The repository is not in the configured allowlist (fail-closed)."""


class RefNotAllowed(GitborrowError):
    """The ref (branch/tag) is not allowed for this repository."""


class CredentialError(GitborrowError):
    """A credential is missing or unreadable. Never contains the secret value."""


class FilterError(GitborrowError):
    """Secret filter aborted the fetch (action='fail')."""


class MirrorUnreachable(GitborrowError):
    """The upstream/mirror could not be reached.

    ``cause`` is one of: 'ssh_key', 'ip_not_allowed', 'timeout', 'unknown'.
    """

    def __init__(self, message: str, cause: str = "unknown") -> None:
        super().__init__(message)
        self.cause = cause


class FetchTimeout(GitborrowError):
    """A git network operation exceeded its timeout."""
