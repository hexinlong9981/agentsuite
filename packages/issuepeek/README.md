# issuepeek

A **read-only issue-tracker connector** for AI agents — `list` / `read` / `search` /
`download` over a pluggable tracker backend, gated by a **fail-closed project
allowlist**, with every call **audited** and results **secret-masked**. Read-only
by design (no create / comment / edit / delete). Built on
[`agent-policy-kit`](../agent-policy-kit)'s `ReadOnlyConnector`.

> Ships a `fake` backend (offline); real tracker backends are added through the
> `register` extension point below.

## Configure (`issuepeek.toml`)

```toml
[backend]
type = "fake"
# credentials for a real backend are NOT here — read from the environment

[projects]             # the allowlist: project -> permitted operations (read-only)
DEMO  = ["list", "read", "search", "download"]
OPS   = ["list", "read"]

[audit]
path = "issuepeek-audit.log"
```

Unknown projects and any write operation are refused (fail-closed).

## Use

```bash
issuepeek --config issuepeek.toml projects
issuepeek --config issuepeek.toml list --project DEMO
issuepeek --config issuepeek.toml read --project DEMO --id 42
issuepeek --config issuepeek.toml search --project DEMO --query timeout
```

```python
from issuepeek.core import Config, IssueTrackerConnector
conn = IssueTrackerConnector.from_config(Config.load("issuepeek.toml"))
conn.read("DEMO", issue_id="42")   # policed, audited, secret-masked
```

## Add a backend

Implement `issuepeek.core.backends.IssueBackend` (`list_projects`, `list_issues`,
`read_issue`, `search`, `download_attachment`) and `register("name", YourBackend)`.
Scope, audit and masking are handled by the connector.

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
