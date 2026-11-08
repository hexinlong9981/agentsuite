# metrics-fetcher (`mfetch`)

Fetch time-series **metrics from pluggable sources to CSV**. The granularity is
chosen automatically so a long range does not return an unreadable number of
points (and warns when the request cannot be met), output is **contained** to a
configured directory, sources are **read-only** and allowlisted, and every fetch
is audited.

> Ships `synthetic` (demos/tests), `csv` (local files),
> `http_json` (any REST/JSON metric API — e.g. AppDynamics or an AWS dashboard
> behind a proxy), and an optional `cloudwatch` source (`pip install
> metrics-fetcher[aws]`). More backends plug in as additional source types.

## What it guarantees

| Guarantee | How |
|---|---|
| **Auto granularity** | the smallest candidate period keeping points ≤ `max_points`; warns if even the coarsest can't |
| **Read-only sources** | sources only list/read metrics |
| **Allowlisted** | unknown source → refused (fail-closed) |
| **Contained output** | CSV filenames are sanitised and written only under the output dir |
| **Audited** | JSON-line log per fetch (period, points, warnings) |

## Configure (`mfetch.toml`)

```toml
[output]
dir = "metrics-out"
max_points = 1440
candidate_periods = [60, 300, 900, 3600, 21600, 86400]

[audit]
path = "mfetch-audit.log"

[sources.demo]
type = "synthetic"
metrics = ["cpu", "mem"]

[sources.exported]
type = "csv"
root = "/path/to/exported-metrics"   # <root>/<namespace>/<name>.csv rows: epoch,value

[sources.dashboard]
type = "http_json"
url_template = "https://metrics.example.com/api/{metric}?from={start}&to={end}&step={period}"
points_key = "points"                 # response: {"points": [{"ts": <epoch>, "value": <num>}, ...]}
ts_key = "ts"
value_key = "value"
auth_env = { Authorization = "DASHBOARD_TOKEN" }   # value read from $DASHBOARD_TOKEN at run time

[sources.cw]
type = "cloudwatch"                   # needs metrics-fetcher[aws]; region/creds from the AWS env
region = "us-east-1"
namespace = "AWS/EC2"
```

## Use

```bash
mfetch --config mfetch.toml sources
mfetch --config mfetch.toml list-metrics --source exported
mfetch --config mfetch.toml fetch --source exported --namespace host1 --metric cpu \
       --start 2026-11-01T00:00:00 --end 2026-11-02T00:00:00
```

## Add a source

Implement `metrics_fetcher.core.sources.MetricsSource` (`list_metrics`, `fetch`)
and `register("name", YourSource)`. Granularity selection, containment and audit
are provided by the fetcher.

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
