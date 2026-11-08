"""Exception types for procflow."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class ProcflowError(PolicyKitError):
    """Base class for procflow errors."""


class DefinitionError(ProcflowError):
    """A definition (or interface) file could not be read or is malformed."""


class CompileError(ProcflowError):
    """A procedure could not be compiled (carries the findings)."""

    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


class ConfigError(ProcflowError):
    """Runtime configuration is invalid. Messages never contain the offending value."""

    def __init__(self, errors, code: int = 1):
        super().__init__("\n".join(errors))
        self.errors = list(errors)
        self.code = code
