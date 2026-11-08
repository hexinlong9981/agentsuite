"""Exception hierarchy (shares the suite base)."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class McpRuntimeError(PolicyKitError):
    """Base class (also: unknown tool)."""


class ToolNotEnabled(McpRuntimeError):
    """The tool is registered but not listed in the manifest (fail-closed)."""
