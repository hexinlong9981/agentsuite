"""Errors for skillkit (all derive from the shared core's base error)."""

from __future__ import annotations

from agent_policy_kit.errors import PolicyKitError


class SkillkitError(PolicyKitError):
    """Base class for skillkit errors."""


class ParseError(SkillkitError):
    """A definition file could not be parsed or fails schema validation."""


class ScaffoldError(SkillkitError):
    """A scaffold request is invalid or would overwrite / escape the root."""
