"""Errors for reportsite (rooted in the shared agent-policy-kit error base)."""

from __future__ import annotations

from agent_policy_kit import PathEscape, PolicyKitError


class ReportSiteError(PolicyKitError):
    """Base class for reportsite errors."""


class SpecError(ReportSiteError):
    """The site spec is invalid (missing title, duplicate/reserved page id, ...)."""


class PresetError(SpecError):
    """Unknown preset name or invalid preset request."""


class MarkdownError(SpecError):
    """The Markdown input cannot be converted (empty, oversized, no usable structure)."""


class CheckFailed(ReportSiteError):
    """The completion gate reported at least one error-severity finding."""


# Exit codes used by the CLI (mapped with agent_policy_kit.exit_code_for).
EXIT_CODES: dict[type, int] = {
    CheckFailed: 1,
    SpecError: 2,
    PathEscape: 3,
    ReportSiteError: 4,
}
