# Design note

cmdgate addresses a common, business-agnostic need: *letting an automated
client run a fixed, safe set of commands on specific machines*. The reusable
value is the policy engine and the safety net, not any particular command set.

## Architecture

```
cli.py  ──►  core
             runner ─ policy ─ audit
             config ─ types ─ errors
```

- **policy** (`policy.py`) — fail-closed allowlist: a command runs only if a rule
  (program + optional args regex) in an applicable policy matches. Two always-on
  safety layers: shell metacharacters are refused (unless `allow_shell`), and a
  `read_only` policy refuses an irreversible-program set even if a rule matches.
- **runner** (`runner.py`) — selects the policy for the target, evaluates, then
  executes as an **argv vector (no shell)** locally, or over SSH as
  `ssh <host> -- <argv>`. Per-command timeout; output capped.
- **config** (`config.py`) — targets and policies are allowlists; a policy that
  references an unknown target is rejected at load.
- **audit** (`audit.py`) — JSON lines; denials recorded with the reason (so you
  can see *why* something was refused, not just that it was).
