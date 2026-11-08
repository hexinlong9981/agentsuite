# Configure credentials and scopes

Two things must be set up before any agentsuite tool does real work:

1. **Credentials**: how a tool authenticates to a system, kept outside the repo.
2. **Scopes**: exactly which targets, commands, hosts, folders and operations are
   allowed. Nothing is allowed until you say so (fail-closed).

This guide is generic: all names, hosts and variables below are placeholders.
For installation see [`INSTALL_CLAUDE.md`](INSTALL_CLAUDE.md).

## 1. Principles

- **No secrets in the repo.** Code, config, tests and docs hold no credentials.
  Configuration only *references* an external store (an environment variable
  name, a file path, a secret manager).
- **Values never leave the process boundary.** A secret is never returned to the
  caller, printed, written to the audit log, or placed on a command line. It is
  handed straight to a subprocess environment (or read inside a backend at
  connect time) and nowhere else.
- **Keep secrets out of config files.** Put a reference (for example an env-var
  name such as `OPS_TOTP_SECRET`) in the config, never the value. Connection
  strings should omit the password; the repo scanner flags quoted
  password/token/key literals.
- **Capability is explicit.** Tools are read-only by default; writes need an
  explicit opt-in, an allowlist and (where it matters) human approval.
- **Everything is audited and masked.** See section 8.

## 2. The credential model

`agent_policy_kit` defines a `CredentialStore` that resolves a *target name* to a
`CredentialHandle`. The handle carries only `env`, a dict of environment
additions for a subprocess. There is no method that returns a secret value.

| Store | Source of values | Use when |
|---|---|---|
| `NullCredentialStore` | none (empty handle) | the target needs no secret, or auth is ambient (an agent socket, instance role) |
| `EnvPassthroughStore(spec)` | named environment variables in the parent process | secrets are injected by your shell, CI or a launcher |
| `FileCredentialStore(path, ...)` | an isolated file outside the repository | you keep a per-user secrets file with restricted permissions |

### EnvPassthroughStore

`spec` maps the variable the subprocess will see to the variable to read it from.
Missing sources are skipped.

```python
from agent_policy_kit import EnvPassthroughStore

store = EnvPassthroughStore({"GIT_TOKEN": "MY_CI_TOKEN"})   # subprocess name -> source name
handle = store.resolve("repo-a")                            # handle.env only; never printed
# merge into a subprocess environment, nowhere else:
#   subprocess.run(argv, env={**os.environ, **handle.env})
```

```bash
export MY_CI_TOKEN=<your-token-here>      # set in the shell, not in any file in the repo
```

### FileCredentialStore

The file holds `KEY=value` lines (or one JSON object) and lives **outside** the
repository. With `strict_permissions=True` the store refuses to read a file that
is accessible to group or others (mode must be `0600`-like).

```bash
install -m 600 /dev/null ~/.config/agentsuite/credentials.env
$EDITOR ~/.config/agentsuite/credentials.env     # add KEY=value lines; never commit this file
```

```text
# ~/.config/agentsuite/credentials.env  (placeholders only)
DB_USER=<your-db-user>
DB_PASSWORD=<your-db-password>
```

```python
import os
from agent_policy_kit import FileCredentialStore

store = FileCredentialStore(
    os.path.expanduser("~/.config/agentsuite/credentials.env"),
    strict_permissions=True,                              # insecure mode -> PermissionError
    target_map={                                          # per-target: subprocess var -> key in the file
        "reporting-db": {"PGUSER": "DB_USER", "PGPASSWORD": "DB_PASSWORD"},
    },
)
handle = store.resolve("reporting-db")    # only the mapped variables
```

Without an entry in `target_map`, a target receives all keys in the file; prefer
an explicit `target_map` so each target sees only what it needs.

### How a tool receives it

A tool asks the store for a handle and passes `handle.env` to the process it
spawns. The agent calling the tool sees only the tool's result, which is masked.
`mcp-runtime` exposes no tool that returns credentials, so a hosted tool cannot
be used to read them back.

## 3. Enabling scopes (fail-closed)

An unknown target, command, host, folder, channel or project is refused. Each
tool has its own config file (TOML); the shapes below are the minimum. Use
`--config <file>` or the tool's `*_CONFIG` environment variable.

### selectgate (SQL)

```toml
[limits]
max_scanned_rows = 1000000      # plan gate: EXPLAIN first, refuse over budget
timeout_seconds  = 30
max_rows         = 1000

[audit]
path = "selectgate-audit.log"

[targets.reporting]
driver = "postgres"
dsn    = "postgresql://<your-db-user>@db.example.invalid:5432/appdb"   # no password here
```

Only the listed targets exist. Provide the password through the environment of
the driver (for PostgreSQL the standard libpq variables) or through a credential
handle, not in the DSN. Statements are limited to a single read-only statement;
writes, DDL and multi-statement input are refused.

**Write-mode is opt-in** and needs a statement allowlist plus approval:

```toml
[write]
enabled = true
allow = ["insert", "update"]    # leading keywords; DDL only if you list it
require_approval = true
```

### cmdgate (commands)

```toml
[targets.build-box]
kind = "ssh"
host = "build.example.invalid"
user = "ci"
jump = ["bastion.example.invalid"]
ssh_options = ["-o", "BatchMode=yes"]

[policies.readonly]
targets   = ["build-box"]
read_only = true                 # irreversible programs refused even if a rule matches
allow = [
  { program = "ls" },
  { program = "git", args_regex = "^(status|log|diff)\\b" },
]
```

Commands run as an argv vector (no shell); anything that matches no `allow` rule
is denied.

### remote-file-sync (rfsync)

```toml
[destinations]
data = "./sync/data"

[sources.remote-app]
transport = "ssh"
host      = "app.example.invalid"
user      = "ops"
jump      = ["bastion.example.invalid"]
path      = "/var/log/app"

[policies.remote_logs]
source  = "remote-app"
include = ["**/*.log"]
exclude = ["**/*.key"]
default_category = "data"
```

The source is read-only and writes are contained under the destination roots.

### web-operator (browser)

```toml
[hosts."app.example.com"]
allow = ["navigate", "read", "interact", "mutate"]

[hosts."*.readonly.example"]
allow = ["navigate", "read"]
```

A host x operation matrix. `mutate` (submit/confirm) additionally needs a preview
and approval at run time (section 7). Log-in credentials are supplied through the
environment or a credential store, never the config.

### Connectors: mailpeek, chatpeek, filepeek, issuepeek

All four are read-only by design and use an allowlist of folders, channels or
projects, each with the operations permitted.

```toml
[backend]
type = "imap"
host = "imap.example.invalid"
# credentials: export MAILPEEK_IMAP_USER and MAILPEEK_IMAP_PASSWORD in the environment

[folders]                        # chatpeek: [channels]   filepeek: [folders]   issuepeek: [projects]
INBOX = ["list", "read", "search", "download"]
Sent  = ["list", "read"]
```

```bash
export MAILPEEK_IMAP_USER=<your-mailbox-user>
export MAILPEEK_IMAP_PASSWORD=<your-mailbox-secret>
```

Anything not listed is refused. API credentials for other backends are read from
the environment or a secret store at connect time.

### term-operator (interactive terminal)

```toml
[sessions]                       # session -> operations
dev       = ["open", "read", "send", "close"]
audit-box = ["open", "read"]     # read-only session

[commands]                       # send allowlist (cmdgate semantics)
read_only = true
allow = [ { program = "ls" }, { program = "git", args_regex = "^(status|log|diff)\\b" } ]
```

`send` must match the allowlist and be approved. See section 6 for the SSH
backend with a bastion and OTP.

### gitborrow (source checkouts)

```toml
[repositories]
allow_any = false                # fail-closed; declare entries instead

[repositories.widget]
url = "https://example.invalid/acme/widget.git"
allowed_refs = ["main"]

[secret_filter]
enabled = true
action  = "mask"                 # mask | delete | fail

[push_guard]
enabled = true                   # no writes back to a remote
```

Checkouts live under an ephemeral root and are discarded after use. Fetch
credentials go only into the git subprocess (for SSH, point `GITBORROW_SSH_KEY`
at a key *file path*; the key material is never read by gitborrow). `procflow`
has the same guard, toggled with `PROCFLOW_PUSH_GUARD_ENABLED` (default `1`,
pushing blocked).

### procflow (procedures)

- Registries (`data_classes.yaml`, `llm_services.yaml`, `agents.yaml`,
  `llm_routing.yaml`) are validated at load. Only declared targets are routable;
  a `local_only` data class is never routed outside.
- Schedules and unattended runs are **disabled by default**; you turn them on
  explicitly. The headless worker refuses human-approval gates and interactive
  capabilities.
- Runtime config has caps and locks (limits, per-run budgets, daily ceilings)
  that child runs can only narrow.
- Credentials stay with the registries' backends; run records and notifications
  are passed through the secret masker. procflow commits locally and **never
  pushes**.

## 4. Provide secrets safely

```bash
# per shell / launcher (nothing written to the repo)
export DB_PASSWORD=<your-db-password>
export PROCFLOW_PUSH_GUARD_ENABLED=1
```

For a long-lived setup prefer an isolated, per-user file (section 2) or your
platform's secret manager, loaded by the process that launches the tool. When you
start Claude Code (or an MCP server) from a shell, only the variables you export
there are visible to it.

## 5. SSH bastion / jump host

`build_ssh_argv` assembles an `ssh` command. It makes no connection and bakes in
no host or credential. `jump` is one hop or a chain and renders as `-J h1,h2`.

```python
from agent_policy_kit import build_ssh_argv

argv = build_ssh_argv(
    "app.example.invalid", user="ops", port=22,
    jump=["bastion1.example.invalid", "bastion2.example.invalid"],
    options=["-o", "BatchMode=yes"],
    remote_argv=["ls", "-l", "/var/log/app"],     # shell-quoted for the remote shell by default
)
# ['ssh', '-p', '22', '-J', 'bastion1...,bastion2...', '-o', 'BatchMode=yes', 'ops@app...', '--', 'ls', '-l', '/var/log/app']
```

In config files this is the `jump = [...]` key on `cmdgate`, `remote-file-sync`
and `term-operator` SSH targets.

## 6. OTP for bastions (TOTP)

When a bastion asks for a one-time code, a provider yields the *current* code.
The TOTP seed is read from the environment or credential store, never from
config, and is never returned or logged.

| Provider | Source of the code |
|---|---|
| `TotpProvider(secret_env, digits=6, period=30)` | RFC 6238 from a base32 seed in the named env var |
| `CallbackOtpProvider(fn)` | a callable (for example prompt a person) |
| `CommandOtpProvider(argv)` | stdout of an external command (argv list, no shell) |

```bash
export OPS_TOTP_SECRET=<your-base32-totp-seed>     # or load via a credential store
```

```python
from agent_policy_kit import TotpProvider

otp = TotpProvider("OPS_TOTP_SECRET")   # the config holds only the variable *name*
code = otp.code()                       # the current code; the seed stays inside
```

`term-operator`'s SSH backend takes the variable name (`otp_env`) in its config:

```toml
[backend]
type = "ssh"
host = "app.example.invalid"
user = "ops"
jump = ["bastion.example.invalid"]
otp_env = "OPS_TOTP_SECRET"
```

## 7. Human approval for writes

Mutating operations (database writes, `mutate` in web-operator, `send` in
term-operator, posts/uploads/creates in the connectors) show a preview and ask
an `Approval` to decide.

| Approval | Behaviour |
|---|---|
| `DenyAll` | refuses everything. **The default.** |
| `Callback(fn)` | `fn(target, operation, preview) -> bool`; route this to a person |
| `AllowAll` | approves everything; tests or trusted automation only |

```python
from agent_policy_kit import Callback

def ask_human(target, operation, preview):
    print(f"[{target}] {operation}\n{preview}")
    return input("approve? [y/N] ").strip().lower() == "y"

approval = Callback(ask_human)
# pass it where the tool takes an approval, e.g.
#   selectgate:    core.run_write(target, sql, config=cfg, approval=approval)
#   web-operator:  Operator(backend=..., policy=cfg.policy, approval=approval, audit=cfg.audit)
#   term-operator: TermOperator.from_config(cfg, approval=approval)
```

Wire the callback to a channel a person actually controls (a terminal prompt, a
ticket, a chat approval). The agent must never be able to answer its own
approval. When running unattended, leave `DenyAll` in place so writes are
refused.

## 8. Audit and masking

- **Audit** (`AuditLog`): an append-only JSON-lines file, set per tool with
  `[audit] path = "..."`. It records each action and each denial with the reason
  (for example `not enabled`, `scope`, `readonly`), the target and the result.
  It records no secret values, and `mcp-runtime` logs the tool name and result,
  not the arguments' secrets.
- **Masking** (`SecretMasker`): defense in depth for text and structured
  results. It redacts private-key blocks, credentials embedded in URLs, and
  `KEY=value` lines whose key names a secret; `mask_obj` recurses through
  dicts/lists and redacts values under secret-named keys. Add your own rules:

```python
from agent_policy_kit import SecretMasker

masker = SecretMasker(extra_patterns=[r"corp-[a-z0-9]{16}"])
masker.mask_text("connect to postgresql://user:<pw>@db.example.invalid/app")
```

Masking is best effort: do not rely on it to make it acceptable to place a secret
where an agent can read it. Keep secrets out of reach in the first place.

## 9. Checklist

- [ ] Config files contain only references (env-var names, file paths), no values.
- [ ] DSNs and URLs carry no password; secrets come from the environment, a
      credential file (`0600`, outside the repo) or a secret manager.
- [ ] Every target, command, host, folder, channel and project you need is listed;
      nothing broader.
- [ ] Policies are `read_only = true` unless a write is required; writes have an
      allowlist and `require_approval = true`.
- [ ] Approval is `DenyAll` or a human-routed `Callback`, never `AllowAll` outside tests.
- [ ] Push guard and secret filter are enabled for source access.
- [ ] Schedules/unattended runs remain off unless deliberately enabled.
- [ ] An audit path is set for every tool and the logs are kept outside the repo.
- [ ] Credential files, `.env` files and audit logs are in `.gitignore`.

**Never commit secrets.** If one is committed by mistake, treat it as exposed:
rotate it, then remove it from history. Before sharing or publishing, run the
scanner from the repository root and require zero violations:

```bash
python3 scripts/pre_publish_scan.py
```

The scanner catches patterns (private keys, quoted credential literals, private
IP addresses, internal hostnames). It does not recognize organization or customer
names, so also review the diff by eye.
