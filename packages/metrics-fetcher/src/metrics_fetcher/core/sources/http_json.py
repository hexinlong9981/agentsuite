"""Generic HTTP/JSON metric source (read-only).

Fetches a time series from an HTTP endpoint that returns JSON. Covers REST /
dashboard-style metric APIs (e.g. AppDynamics, an AWS dashboard behind a proxy)
without bundling any vendor SDK. Offline-testable by injecting a ``transport``.

options:
  url_template : str with {metric} {namespace} {start} {end} {period}
  points_key   : key holding the list of points (default "points"; "" = top level)
  ts_key       : timestamp key in a point (default "ts")
  value_key    : value key in a point (default "value")
  metrics      : optional static list of names for list_metrics
  auth_env     : optional {header: ENV_VAR}; values are read from the environment
                 at fetch time and passed as request headers — never stored,
                 returned, or logged by this tool.
"""

from __future__ import annotations

import json
import os

from ..errors import SourceError
from ..types import DataPoint, MetricRef, Series
from . import MetricsSource


def _default_transport(url: str, headers: dict) -> str:  # pragma: no cover - network
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


class HttpJsonSource(MetricsSource):
    def __init__(self, name: str, options: dict, transport=None) -> None:
        self.name = name
        self.options = options or {}
        self._transport = transport or _default_transport

    def _headers(self) -> dict:
        headers = {}
        for header, env_var in (self.options.get("auth_env") or {}).items():
            value = os.environ.get(env_var)
            if value is not None:
                headers[header] = value
        return headers

    def list_metrics(self) -> list[MetricRef]:
        return [MetricRef(self.name, m) for m in self.options.get("metrics", [])]

    def fetch(self, ref: MetricRef, start: int, end: int, period: int) -> Series:
        template = self.options.get("url_template")
        if not template:
            raise SourceError("http_json source requires 'url_template'")
        url = template.format(metric=ref.name, namespace=ref.namespace,
                              start=start, end=end, period=period)
        try:
            raw = self._transport(url, self._headers())
            data = json.loads(raw)
        except Exception as exc:
            raise SourceError(f"http_json fetch/parse failed: {type(exc).__name__}") from None

        points_key = self.options.get("points_key", "points")
        ts_key = self.options.get("ts_key", "ts")
        value_key = self.options.get("value_key", "value")
        rows = data.get(points_key) if points_key else data
        if rows is None:
            raise SourceError(f"http_json: points_key {points_key!r} not found in response")

        buckets: dict[int, float | None] = {}
        for row in rows:
            try:
                ts = int(row[ts_key])
            except (KeyError, TypeError, ValueError):
                continue
            if not (start <= ts < end):
                continue
            raw_val = row.get(value_key)
            try:
                value = None if raw_val is None else float(raw_val)
            except (TypeError, ValueError):
                value = None
            buckets[(ts // period) * period] = value
        points = [DataPoint(ts=b, value=buckets[b]) for b in sorted(buckets)]
        return Series(ref=ref, period=period, points=points)
