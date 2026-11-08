# term-operator

Policy-gated **interactive terminal / console** automation for AI agents.
Reading a session's screen/output is a read operation; **sending a command is a
write** that must (1) match a **command allowlist** (reused from
[`cmdgate`](../cmdgate)), and (2) be **approved** (preview → human). Sessions are
a fail-closed allowlist and every action is audited.

> Backends: `fake` (offline), `local_pty` (a real shell in a
> PTY), and `ssh` — an interactive session over a **bastion / jump-host chain**
> that **auto-answers an OTP prompt** (TOTP from a secret in the credential
> store). A GUI-terminal-window client (e.g. an RLogin-style app) fits as another
> backend. For simple non-interactive "run a command on a host", use `cmdgate`.

### SSH backend (bastion + OTP)

```toml
[backend]
type = "ssh"
host = "app.example.invalid"
user = "ops"
jump = ["bastion.example.invalid"]   # ssh -J bastion...
otp_env = "OPS_TOTP_SECRET"          # base32 TOTP seed read from this env var (never in the repo)
```

## Configure (`term_operator.toml`)

```toml
[backend]
type = "fake"          # or "local_pty"

[sessions]             # session -> allowed operations (open/read/close/send)
dev = ["open", "read", "send", "close"]
audit-box = ["open", "read"]          # read-only session (no send)

[commands]             # the send allowlist (cmdgate semantics); read_only blocks irreversible
read_only = true
allow = [
  { program = "ls" },
  { program = "git", args_regex = "^(status|log|diff)\\b" },
]

[audit]
path = "term-operator-audit.log"
```

## Use

```python
from term_operator.core import Config, TermOperator, Callback

def approve(session, operation, command):
    return input(f"send to {session}: {command}\napprove? [y/N] ").strip().lower() == "y"

op = TermOperator.from_config(Config.load("term_operator.toml"), approval=Callback(approve))
op.open("dev")
op.read("dev")                       # read-only, masked
op.send("dev", ["git", "status"])    # allowlist -> preview -> approve -> run
```

```bash
term-operator --config term_operator.toml sessions
term-operator --config term_operator.toml read --session dev
term-operator --config term_operator.toml send --session dev -- git status   # prompts to approve
```

## Add a backend

Implement `term_operator.core.backends.TerminalBackend` (`open` / `read` / `send`
/ `close`) and `register("name", YourBackend)`. The session matrix, command
allowlist, approval and audit are provided by the operator.

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
