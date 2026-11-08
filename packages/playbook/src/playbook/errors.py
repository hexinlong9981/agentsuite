"""Error hierarchy for playbook."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class PlaybookError(PolicyKitError):
    """Base class for all playbook errors."""


class PrincipleNotFound(PlaybookError):
    """No principle with that id."""


class InvalidPrinciple(PlaybookError):
    """A principle is malformed."""
