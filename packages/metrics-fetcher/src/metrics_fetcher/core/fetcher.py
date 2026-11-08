"""Fetch a metric: choose period -> read from source -> write contained CSV."""

from __future__ import annotations

from . import audit, containment, writer
from .config import Config
from .errors import InvalidRange
from .plan import choose_period
from .sources import make_source
from .types import FetchResult, MetricRef


def fetch(source_name: str, metric: str, start: int, end: int, *, config: Config,
          namespace: str = "", max_points: int | None = None, write: bool = True) -> FetchResult:
    if end <= start:
        raise InvalidRange(f"end ({end}) must be greater than start ({start})")
    src_cfg = config.get_source(source_name)
    source = make_source(src_cfg)

    budget = max_points or config.output.max_points
    plan = choose_period(start, end, budget, config.candidate_periods)
    ref = MetricRef(source=source_name, name=metric, namespace=namespace)
    series = source.fetch(ref, start, end, plan.period)

    csv_text = writer.to_csv(series)
    dest = containment.safe_join(
        config.output.dir,
        f"{source_name}__{namespace or 'default'}__{metric}__{start}-{end}.csv",
    )
    if write:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(csv_text, encoding="utf-8")

    audit.log(config, {"action": "fetch", "source": source_name, "metric": metric,
                       "namespace": namespace, "period": plan.period,
                       "points": len(series.points),
                       "result": "ok" if write else "dry_run", "warnings": plan.warnings})
    return FetchResult(series=series, plan=plan, csv_path=str(dest), points=len(series.points))


def list_metrics(source_name: str, *, config: Config):
    return make_source(config.get_source(source_name)).list_metrics()
