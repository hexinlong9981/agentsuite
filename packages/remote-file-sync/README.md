# remote-file-sync (`rfsync`)

Pull files from a source into **local destinations by policy**: each file is
matched, classified (e.g. *config* vs *data*) and routed to a destination root.
The source is **read-only** (files are only read from it), writes are
**contained** to configured roots (no path traversal), and sources/policies are
allowlists.

> Transports: **local filesystem** and **SSH** with bastion /
> jump-host chain (`-J`) and port support.

## What it guarantees

| Guarantee | How |
|---|---|
| **Read-only source** | the transport only lists/reads; nothing is written back |
| **Classified routing** | each file → a category (first matching rule, else default) → a destination root |
| **Contained writes** | a file may only be written under its destination root (traversal refused) |
| **Allowlisted** | unknown policy/source/category → refused (fail-closed) |
| **Bounded** | file-count and byte limits; optional per-file size cap |
| **Bastion / Jump host** | SSH transport supports multi-hop bastion chains (`jump = ["b1", "b2"]`) |
| **Audited** | JSON-line log of each sync |

## Configure (`rfsync.toml`)

```toml
[limits]
max_files = 10000
max_bytes = 1000000000

[audit]
path = "rfsync-audit.log"

[destinations]
config = "/var/lib/rfsync/config"
data   = "/var/lib/rfsync/data"

# Local source
[sources.server1]
transport = "local"
root = "/mnt/server1-snapshot"

# Remote SSH source (with bastion / jump host chain)
[sources.remote-app]
transport   = "ssh"
host        = "app.internal.example.invalid"
user        = "ops"
port        = 22
jump        = ["bastion.example.invalid"]       # ssh -J bastion...
path        = "/var/log/app"
ssh_options = ["-o", "BatchMode=yes"]

[policies.etc_and_logs]
source = "server1"
include = ["etc/**", "var/log/**"]
exclude = ["**/*.key"]
default_category = "data"
[[policies.etc_and_logs.classify]]
pattern = "etc/**"
category = "config"

[policies.remote_logs]
source = "remote-app"
include = ["**/*.log"]
exclude = ["**/*.gz"]
default_category = "data"
```

## Use

```bash
rfsync --config rfsync.toml policies
rfsync --config rfsync.toml sync --policy etc_and_logs --dry-run
rfsync --config rfsync.toml sync --policy etc_and_logs
rfsync --config rfsync.toml sync --policy remote_logs
```

## Library

```python
from remote_file_sync import core

cfg = core.Config.load("rfsync.toml")
res = core.sync("remote_logs", config=cfg)
print(f"Fetched {len(res.fetched)} files ({res.bytes_fetched} bytes)")
```
