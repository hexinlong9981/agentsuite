"""Pluggable, read-only metric sources."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import SourceConfig
from ..errors import SourceError
from ..types import MetricRef, Series


class MetricsSource(ABC):
    @abstractmethod
    def list_metrics(self) -> list[MetricRef]: ...

    @abstractmethod
    def fetch(self, ref: MetricRef, start: int, end: int, period: int) -> Series: ...


_REGISTRY: dict[str, type] = {}


def register(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def make_source(cfg: SourceConfig) -> MetricsSource:
    if cfg.type not in _REGISTRY:
        raise SourceError(f"no such source type: {cfg.type!r} (available: {sorted(_REGISTRY)})")
    return _REGISTRY[cfg.type](cfg.name, cfg.options)


from .cloudwatch import CloudWatchSource  # noqa: E402
from .csv_source import CsvSource  # noqa: E402
from .http_json import HttpJsonSource  # noqa: E402
from .synthetic import SyntheticSource  # noqa: E402

register("synthetic", SyntheticSource)
register("csv", CsvSource)
register("http_json", HttpJsonSource)
register("cloudwatch", CloudWatchSource)
