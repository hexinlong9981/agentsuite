# cmdgate

Let a person — or an AI agent — run **only allowlisted commands on allowlisted
targets** (local machine or an SSH host). Everything else is denied
(fail-closed). Policies are **read-only by default**: irreversible programs are
refused even if a rule would match. Commands run as an argv vector (no shell),
and every run and denial is audited with its reason.

> Supports local execution and SSH execution.

## What it guarantees

| Guarantee | How |
|---|---|
| **Allowlisted commands** | a command runs only if it matches an `allow` rule (program + optional args regex) in a policy that applies to the target |
| **Allowlisted targets** | unknown target → refused |
| **Read-only by default** | `read_only` policies refuse irreversible programs (`rm`, `dd`, `shutdown`, `kill`, …) even if a rule matches |
| **No shell injection** | commands execute as argv (no shell); arguments with shell metacharacters are refused unless the policy opts in |
| **Bounded** | per-command timeout; output capped and flagged `truncated` |
| **Audited** | JSON-line log of every run and denial (with the deny reason) |

## Configure (`cmdgate.toml`)

```toml
[limits]
timeout_seconds  = 60
max_output_bytes = 1000000

[audit]
path = "cmdgate-audit.log"

[targets.local]
kind = "local"

[targets.build-box]
kind = "ssh"
host = "build.example.invalid"
user = "ci"
port = 22
jump = ["bastion.example.invalid"]        # bastion / jump-host chain (-> ssh -J ...)
ssh_options = ["-o", "BatchMode=yes"]

[policies.readonly]
targets   = ["local", "build-box"]
read_only = true
allow = [
  { program = "ls" },
  { program = "cat" },
  { program = "git", args_regex = "^(status|log|diff|show)\\b" },
]
```

## Use

```bash
cmdgate --config cmdgate.toml targets
cmdgate --config cmdgate.toml allowed --target local
cmdgate --config cmdgate.toml run --target local -- git status
```

## Library

```python
from cmdgate import core
cfg = core.Config.load("cmdgate.toml")
res = core.run("local", ["git", "status"], config=cfg)
print(res.returncode, res.allowed_by, res.stdout)
```

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
