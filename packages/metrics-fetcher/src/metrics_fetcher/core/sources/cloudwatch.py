"""AWS CloudWatch metric source (optional; needs boto3). Read-only.

Install the extra: ``pip install metrics-fetcher[aws]``. Region and credentials
come from the standard AWS environment / config — never from this repo. Not
exercised by the offline test suite (needs boto3 and live AWS).

options: region, namespace (default for refs without one), stat (default "Average"),
metrics (optional static list for list_metrics).
"""

from __future__ import annotations

from ..errors import SourceError
from ..types import DataPoint, MetricRef, Series
from . import MetricsSource


class CloudWatchSource(MetricsSource):
    def __init__(self, name: str, options: dict) -> None:
        self.name = name
        self.options = options or {}

    def _client(self):  # pragma: no cover - needs boto3 + AWS
        try:
            import boto3
        except ModuleNotFoundError:
            raise SourceError(
                "CloudWatch source needs boto3 (pip install metrics-fetcher[aws])") from None
        return boto3.client("cloudwatch", region_name=self.options.get("region"))

    def list_metrics(self) -> list[MetricRef]:
        return [MetricRef(self.name, m) for m in self.options.get("metrics", [])]

    def fetch(self, ref: MetricRef, start: int, end: int, period: int) -> Series:  # pragma: no cover
        from datetime import datetime, timezone
        namespace = ref.namespace or self.options.get("namespace")
        if not namespace:
            raise SourceError("CloudWatch fetch requires a namespace (ref.namespace or options.namespace)")
        client = self._client()
        resp = client.get_metric_data(
            MetricDataQueries=[{
                "Id": "m1",
                "MetricStat": {
                    "Metric": {"Namespace": namespace, "MetricName": ref.name},
                    "Period": period,
                    "Stat": self.options.get("stat", "Average"),
                },
                "ReturnData": True,
            }],
            StartTime=datetime.fromtimestamp(start, timezone.utc),
            EndTime=datetime.fromtimestamp(end, timezone.utc),
        )
        result = resp["MetricDataResults"][0]
        points = [DataPoint(ts=int(t.timestamp()), value=float(v))
                  for t, v in zip(result.get("Timestamps", []), result.get("Values", []))]
        points.sort(key=lambda p: p.ts)
        return Series(ref=ref, period=period, points=points)
