"""CSV source — read metrics from local CSV files (epoch,value per row)."""

from __future__ import annotations

import csv as _csv
from pathlib import Path

from ..errors import SourceError
from ..types import DataPoint, MetricRef, Series
from . import MetricsSource


class CsvSource(MetricsSource):
    def __init__(self, name: str, options: dict) -> None:
        self.name = name
        self.root = Path(options.get("root", "")).expanduser().resolve()

    def list_metrics(self) -> list[MetricRef]:
        refs: list[MetricRef] = []
        if self.root.is_dir():
            for p in sorted(self.root.rglob("*.csv")):
                rel = p.relative_to(self.root)
                namespace = "/".join(rel.parts[:-1])
                refs.append(MetricRef(self.name, rel.stem, namespace))
        return refs

    def _path(self, ref: MetricRef) -> Path:
        rel = f"{ref.namespace}/{ref.name}.csv" if ref.namespace else f"{ref.name}.csv"
        full = (self.root / rel).resolve()
        try:
            full.relative_to(self.root)
        except ValueError:
            raise SourceError(f"metric path escapes source root: {rel}") from None
        return full

    def fetch(self, ref: MetricRef, start: int, end: int, period: int) -> Series:
        path = self._path(ref)
        if not path.is_file():
            raise SourceError(f"metric not found: {ref.namespace}/{ref.name}".lstrip("/"))
        buckets: dict[int, float | None] = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for row in _csv.reader(fh):
                if not row or not row[0].strip().lstrip("-").isdigit():
                    continue
                ts = int(row[0])
                if not (start <= ts < end):
                    continue
                try:
                    value: float | None = float(row[1])
                except (IndexError, ValueError):
                    value = None
                buckets[(ts // period) * period] = value       # last point per bucket wins
        points = [DataPoint(ts=b, value=buckets[b]) for b in sorted(buckets)]
        return Series(ref=ref, period=period, points=points)
