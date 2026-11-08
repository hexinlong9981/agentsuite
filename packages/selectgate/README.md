# selectgate

A **read-only SQL gateway**: let a person — or an AI agent — run `SELECT`s
against your databases without being able to write, run away with a full-table
scan, or hang the server. Write-mode is strictly opt-in behind statement allowlists
and human approval.

> Ships drivers for **SQLite** (stdlib, offline-testable),
> **PostgreSQL** (psycopg/pg8000), and **MySQL / MariaDB** (pymysql/mysql-connector)
> with dialect-specific plan gates (`EXPLAIN QUERY PLAN`, `EXPLAIN (FORMAT JSON)`,
> and `EXPLAIN FORMAT=JSON`).

## What it guarantees

| Guarantee | How |
|---|---|
| **Read-only by default** | statement guard accepts only a *single* read-only statement (SELECT/WITH/EXPLAIN/VALUES/read-PRAGMA); writes, DDL and multiple statements are refused (fail-closed) |
| **Bounded cost (plan gate)** | the query is `EXPLAIN`-ed first; if it would scan more rows than the budget it is refused *before* running |
| **Bounded time** | execution is interrupted past a timeout (`statement_timeout` on Postgres) |
| **Bounded output** | rows are capped; results flag `truncated` |
| **Allowlisted targets** | only configured targets are reachable (unknown target → refused) |
| **Write-mode (opt-in)** | writes require `[write].enabled = true`, statement shape in `allow = [...]`, and preview → human approval |
| **Audited** | every query, write, and refusal is logged (no values) |

## Supported Drivers & Plan Gates

| Driver | Connection / Package | EXPLAIN Dialect | Full-Scan Detection |
|---|---|---|---|
| `sqlite` | stdlib `sqlite3` | `EXPLAIN QUERY PLAN` | `SCAN <table>` (cost = table rowcount) |
| `postgres` / `postgresql` | `psycopg` / `psycopg2` / `pg8000` | `EXPLAIN (FORMAT JSON)` | `Seq Scan on <relation>` (cost = plan rows) |
| `mysql` / `mariadb` | `pymysql` / `mysqlclient` / `mysql.connector` | `EXPLAIN FORMAT=JSON` | `access_type = ALL` (cost = rows examined) |

## Install

```bash
pip install -e .              # core + CLI (SQLite driver)
pip install -e '.[postgres]'  # + PostgreSQL driver (psycopg)
pip install -e '.[mysql]'     # + MySQL driver (pymysql)
```

Python ≥ 3.11.

## Configure (`selectgate.toml`)

```toml
[limits]
max_scanned_rows = 1000000
timeout_seconds  = 30
max_rows         = 1000

[audit]
path = "selectgate-audit.log"

[targets.reporting]
driver = "sqlite"
dsn    = "/path/to/reporting.db"

[targets.prod-pg]
driver = "postgres"
dsn    = "postgresql://user@pg.example.invalid:5432/appdb"

[targets.prod-mysql]
driver = "mysql"
dsn    = "mysql://user@mysql.example.invalid:3306/appdb"

# Writes are OFF by default. Enable them explicitly, with an allowlist of leading
# keywords and (by default) human approval. DDL is allowed only if you list it.
[write]
enabled = true
allow = ["insert", "update", "delete"]
require_approval = true
```

## Use

Read:

```bash
selectgate --config selectgate.toml targets
selectgate --config selectgate.toml explain --target reporting --sql "SELECT * FROM orders WHERE customer_id = 42"
selectgate --config selectgate.toml query   --target reporting --sql "SELECT * FROM orders WHERE customer_id = 42" --max-rows 100
```

Write (opt-in; prompts for approval on the CLI):

```bash
selectgate --config selectgate.toml write --target reporting --sql "UPDATE orders SET status='x' WHERE id=1"
```

```python
from selectgate import core
cfg = core.Config.load("selectgate.toml")
result, estimate = core.run_select("reporting", "SELECT 1", config=cfg)

# write-mode: refused unless [write].enabled, the statement is in the allowlist, and approved
res = core.run_write("reporting", "INSERT INTO audit(x) VALUES (1)",
                     config=cfg, approval=core.AllowAll())   # use a Callback for a human
```
