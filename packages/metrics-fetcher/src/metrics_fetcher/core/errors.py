"""Exception hierarchy."""

from __future__ import annotations

from agent_policy_kit import PolicyKitError


class MetricsFetcherError(PolicyKitError):
    """Base class."""


class SourceNotAllowed(MetricsFetcherError):
    """No such source (fail-closed)."""


class SourceError(MetricsFetcherError):
    """A source failed to list/fetch (or is not implemented)."""


class OutputError(MetricsFetcherError):
    """Output path escapes the configured output directory."""


class InvalidRange(MetricsFetcherError):
    """start/end time range is invalid."""
