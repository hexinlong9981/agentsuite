# Design note

metrics-fetcher provides "pull metrics for a window into a CSV, at a sane
resolution". The reusable value is the granularity planner, the pluggable
read-only source interface, and contained output — not any particular backend.

## Architecture

```
cli.py  ──►  core.fetcher.fetch(...)
             plan (granularity) ─ sources/ (read-only; synthetic, csv built-in)
             writer (CSV) ─ containment ─ audit
             config ─ types ─ errors
```

- **plan** (`plan.py`) — picks the smallest candidate period keeping the point
  count within `max_points`; warns when even the coarsest cannot. This avoids
  grabbing a too-coarse or too-huge series.
- **sources** (`sources/`) — `MetricsSource` with `list_metrics()` + `fetch()`;
  read-only. `synthetic` and `csv` ship; backends like CloudWatch/AppDynamics are
  additional source types.
- **writer** (`writer.py`) — a stable `epoch,iso,value` CSV.
- **containment** (`containment.py`) — filenames are sanitised; output stays under
  the configured directory.
- **config** (`config.py`) — sources are an allowlist (unknown → refused).
