# Install agentsuite and use it with Claude Code

agentsuite is a set of small, safe-by-default tools for AI agents. Every package
is independently installable and shares one governance core
(`agent-policy-kit`: fail-closed scope, approval, audit, masking, credential
isolation). This guide installs the suite and shows three ways to use it from
[Claude Code](https://claude.com/claude-code). For credentials and scopes, read
[`SECRETS.md`](SECRETS.md) next.

## 1. Prerequisites

| Need | Notes |
|---|---|
| Python 3.11 or newer | `tomllib` is used to read TOML config |
| git | required by `gitborrow` |
| `uv` (optional) | easiest way to install the whole workspace |

Optional extras, installed only when you need them:

| Extra | Package | Enables |
|---|---|---|
| `mcp` | `mcp-runtime`, `gitborrow` | serving over MCP (`pip install 'mcp-runtime[mcp]'`) |
| `markdown` | `reportsite` | Markdown input for report sites |
| `postgres` / `mysql` | `selectgate` | PostgreSQL / MySQL drivers (SQLite is built in) |
| `playwright` | `web-operator` | a real browser backend |

## 2. Install from source

```bash
git clone <your-agentsuite-repo-url> agentsuite
cd agentsuite
```

**Option A: the uv workspace (everything at once)**

```bash
uv sync
uv run selectgate --help
```

**Option B: per-package editable installs**

Each package is its own distribution. `agent-policy-kit` is a dependency of every
other package, so install it first (or let your resolver pull it in):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e packages/agent-policy-kit
pip install -e packages/selectgate            # core + CLI (SQLite driver)
pip install -e 'packages/selectgate[postgres]'
pip install -e 'packages/mcp-runtime[mcp]'
pip install -e packages/gitborrow
```

Install only the tools you need; none of them requires the others.

## 3. Three ways to use it with Claude Code

### 3.1 As one MCP server (`mcp-runtime`)

`mcp-runtime` hosts any number of tools behind a single MCP server. Only tools
listed in a manifest are exposed (fail-closed), every call is audited, and there
are no tools that return credentials.

**Step 1. Write a launcher** (`serve.py`) that registers tools and builds the server:

```python
from mcp_runtime.core import Config, Runtime, ToolRegistry, ToolSpec
from selectgate import core as sg

cfg = sg.Config.load("selectgate.toml")

reg = ToolRegistry()
reg.register(ToolSpec(
    "sql_query",
    lambda target, sql: sg.run_select(target, sql, config=cfg)[0].__dict__,
    "run a read-only SQL query",
))

rt = Runtime(reg, Config.load("mcp_runtime.toml"))   # manifest decides what is exposed

if __name__ == "__main__":
    rt.build_mcp().run()          # stdio server; needs the 'mcp' extra
```

`ToolSpec(name, handler, description)` wraps a plain Python callable.
`Runtime.enabled_tools()` returns the registered tools that the manifest also
enables; `Runtime.call(name, **kwargs)` dispatches one, audited. Calling a tool
that is not in the manifest raises `ToolNotEnabled`.

**Step 2. Write the manifest** (`mcp_runtime.toml`):

```toml
[tools]
enabled = ["sql_query"]          # fail-closed: anything not listed is refused

[audit]
path = "mcp-runtime-audit.log"
```

`Config.load()` reads the path you pass, else `$MCP_RUNTIME_CONFIG`, else
`./mcp_runtime.toml`, else an empty manifest (nothing exposed).

**Step 3. Inspect the manifest** with the CLI. The `mcp-runtime` command is an
inspection tool: it prints the enabled tool names. Serving is done by your
launcher (`build_mcp().run()`), because the tools are registered in code.

```bash
mcp-runtime --config mcp_runtime.toml manifest
mcp-runtime --config mcp_runtime.toml --json manifest
```

**Step 4. Register the server in Claude Code.** Either a project-level
`.mcp.json` (stdio server):

```json
{
  "mcpServers": {
    "agentsuite": {
      "command": "python3",
      "args": ["serve.py"],
      "env": {
        "MCP_RUNTIME_CONFIG": "mcp_runtime.toml"
      }
    }
  }
}
```

or the command line (everything after `--` is the server command):

```bash
claude mcp add agentsuite --scope project -- python3 serve.py
claude mcp list
```

Use the interpreter from the virtual environment where you installed the
packages (for example `.venv/bin/python`). Secrets are never put in `.mcp.json`;
export them in the shell that starts Claude Code, or use a credential store (see
[`SECRETS.md`](SECRETS.md)).

### 3.2 As CLIs

Each package installs a console command. Use them directly in a terminal, or let
Claude Code call them through its Bash tool (allow the specific commands in your
Claude Code permissions).

| Package | Command | Package | Command |
|---|---|---|---|
| `selectgate` | `selectgate` | `mailpeek` | `mailpeek` |
| `cmdgate` | `cmdgate` | `chatpeek` | `chatpeek` |
| `gitborrow` | `gitborrow` | `filepeek` | `filepeek` |
| `remote-file-sync` | `rfsync` | `issuepeek` | `issuepeek` |
| `metrics-fetcher` | `mfetch` | `term-operator` | `term-operator` |
| `web-operator` | `weboperator` | `procflow` | `procflow` |
| `knowledgebase` | `kb` | `playbook` | `playbook` |
| `agentflow` | `agentflow` | `skillkit` | `skillkit` |
| `reportsite` | `reportsite` | `mcp-runtime` | `mcp-runtime` |

Example, read-only queries (a config file with an allowlisted target is
required; see [`SECRETS.md`](SECRETS.md)):

```bash
selectgate --config selectgate.toml targets
selectgate --config selectgate.toml explain --target reporting --sql "SELECT * FROM orders WHERE customer_id = 42"
selectgate --config selectgate.toml query   --target reporting --sql "SELECT * FROM orders WHERE customer_id = 42" --max-rows 100

gitborrow --config gitborrow.toml fetch https://example.invalid/acme/widget.git --ref main
gitborrow --config gitborrow.toml search widget "Calculator"
gitborrow --config gitborrow.toml release widget
```

Every command accepts `--help`. Refusals (unknown target, write attempt,
over-budget scan) are explicit errors and are written to the audit log.

### 3.3 As skills (optional)

A Claude Code skill is a `SKILL.md` that tells the agent when and how to call a
command. `skillkit` provides conventions, a consistency linter and a scaffolder
for skills and sub-agent definitions, so you can wrap the CLIs above:

```bash
skillkit scaffold --help        # create a new skill or agent from a template
skillkit lint <skills-dir>      # print findings (exit 1 on errors)
skillkit check <skills-dir>     # one-line verdict for CI
```

Keep skills about *when and in what order* to act. Policy (which commands, SQL,
hosts or folders are allowed) belongs in each tool's config file, so it is
enforced in code, not by prompt text.

## 4. Verify the install

```bash
selectgate --help                                     # CLI is on PATH
python3 -c "import agent_policy_kit, mcp_runtime"     # core imports
mcp-runtime --config mcp_runtime.toml manifest        # lists enabled tools
python3 -m pytest                                      # from the repo root (dev extra: pytest)
python3 scripts/pre_publish_scan.py                    # repo hygiene scan
```

In Claude Code, run `claude mcp list` to see the server and `/mcp` inside a
session to see its tools. With an empty or missing manifest the list is empty:
that is the fail-closed default, not an error.

## 5. Minimal end-to-end example

A read-only SQLite query exposed to Claude Code.

```bash
# 1. a throwaway database
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("demo.db")
c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, customer_id INTEGER)")
c.execute("INSERT INTO orders (customer_id) VALUES (42)")
c.commit()
PY

# 2. selectgate config: one allowlisted target, writes stay off
cat > selectgate.toml <<'TOML'
[limits]
max_scanned_rows = 100000
timeout_seconds  = 10
max_rows         = 100

[audit]
path = "selectgate-audit.log"

[targets.demo]
driver = "sqlite"
dsn    = "demo.db"
TOML

selectgate --config selectgate.toml query --target demo --sql "SELECT * FROM orders"

# 3. manifest + launcher from section 3.1 (expose only sql_query)
printf '[tools]\nenabled = ["sql_query"]\n\n[audit]\npath = "mcp-runtime-audit.log"\n' > mcp_runtime.toml

# 4. register and use
claude mcp add agentsuite --scope project -- python3 serve.py
```

Then ask Claude Code to "list the orders for customer 42". It can only call
`sql_query` against the `demo` target; a write statement, an unknown target or an
unlisted tool is refused and recorded in the audit logs.

## Next

- [`SECRETS.md`](SECRETS.md): configure credentials and scopes.
- [`ARCHITECTURE.md`](ARCHITECTURE.md): the policy model and package catalogue.
