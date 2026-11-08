"""Exception hierarchy (shares the suite base)."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class AgentflowError(PolicyKitError):
    """Base class."""


class RunNotFound(AgentflowError):
    """No such run under the root."""
