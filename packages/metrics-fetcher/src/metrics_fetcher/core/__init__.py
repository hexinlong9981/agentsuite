"""Public core API."""

from __future__ import annotations

from .config import Config, OutputConfig, SourceConfig
from .errors import (
    InvalidRange,
    MetricsFetcherError,
    OutputError,
    SourceError,
    SourceNotAllowed,
)
from .fetcher import fetch, list_metrics
from .plan import choose_period
from .types import DataPoint, FetchPlan, FetchResult, MetricRef, Series

__all__ = [
    "Config", "SourceConfig", "OutputConfig",
    "fetch", "list_metrics", "choose_period",
    "MetricRef", "Series", "DataPoint", "FetchPlan", "FetchResult",
    "MetricsFetcherError", "SourceNotAllowed", "SourceError", "OutputError", "InvalidRange",
]
