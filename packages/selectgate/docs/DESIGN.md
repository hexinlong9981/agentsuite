# Design note

selectgate addresses a common, business-agnostic pattern: *letting an
automated client read a database safely*. The reusable value is the set of
guards, not any particular schema.

## Architecture

```
cli.py  ──►  core (the guarantees)
             gateway ─ statement ─ plangate ─ audit
             drivers/ (pluggable: sqlite, postgres, mysql)
             config ─ types ─ errors
```

- **statement guard** (`statement.py`) — strips comments/strings, then requires a
  single statement whose leading keyword is read-only and which contains no
  write/DDL keyword. Conservative (fail-closed): a column literally named like a
  write keyword is refused rather than risk a write.
- **plan gate** (`plangate.py` + `Driver.explain`) — the driver returns a
  `PlanEstimate` (rows a full scan would touch + which tables). The gate refuses
  queries over the configured budget *before* execution. This is the core idea
  of the gateway.
- **timeout / row cap** (`Driver.execute`) — long queries are interrupted; output
  is capped and flagged `truncated`.
- **targets** (`config.py`) — an allowlist; unknown targets are refused.
- **audit** (`audit.py`) — JSON lines, refusals included, never query values.
