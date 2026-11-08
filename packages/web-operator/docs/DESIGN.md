# Design note

web-operator provides "let an agent drive a browser, but only within a
declared envelope, and never commit a change without a human seeing it first".
The reusable value is the policy matrix and the mutate-approval workflow, not any
particular site.

## Architecture

```
cli.py (inspection)        Operator.operate(host, operation, **params)
                             -> HostPolicy.require(host, category)   # fail-closed
                             -> (mutate) backend.describe -> Approval.request  # preview -> human
                             -> backend.dispatch(...)                # pluggable; fake / playwright
                             -> AuditLog.record(...)                 # incl. denials
```

- **policy** (`policy.py`) — host × category matrix; `fnmatch` host globs; unknown
  host or category refused.
- **approval** (`approval.py`) — `DenyAll` (default), `AllowAll` (tests), or
  `Callback` (prompt a human). Only `mutate` operations consult it.
- **backends** (`backends/`) — `BrowserBackend` with `dispatch` + `describe`; a
  `FakeBackend` ships for offline use; Playwright is an optional extra.
- **operator** (`operator.py`) — composes matrix → approval (mutate only) → backend
  → audit; a denied matrix check never reaches the backend.

This mirrors the `agent-policy-kit` governance pattern, specialised to a
`host × operation` surface with an approval step for writes.
