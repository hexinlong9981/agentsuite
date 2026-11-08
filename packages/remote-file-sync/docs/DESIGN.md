# Design note

remote-file-sync provides "pull a snapshot of a machine's files into the
right local places, safely". The reusable value is the policy — classify and
route files, keep the source read-only, and contain the writes — not any
particular path layout.

## Architecture

```
cli.py  ──►  core.engine.sync(policy)
             transports (read-only; local and ssh)
             classify ─ containment ─ audit
             config ─ types ─ errors
```

- **transports** (`transports/`) — read-only `list()` + `read()`. `local` and `ssh` ship. A transport never writes to the source.
- **classify** (`classify.py`) — include/exclude globs, then a first-match
  classification to a category; unmatched files fall through to the default.
- **containment** (`containment.py`) — the destination path must stay under its
  category's destination root (traversal refused).
- **config** (`config.py`) — sources, destinations and policies are allowlists;
  a policy that uses a category with no destination, or an unknown source, is
  rejected at load.
- **engine** (`engine.py`) — lists the source, filters, classifies, contains,
  copies to the destination; enforces file/byte limits; `dry_run` reports
  without writing; audits the run.
