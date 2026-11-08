# filepeek

A **read-only file store connector** for AI agents — `list` / `read` / `search` /
`download` over a pluggable file store backend, gated by a **fail-closed folder
allowlist**, with every call **audited** and results **secret-masked**. Read-only
by design (no upload / delete / move). Built on
[`agent-policy-kit`](../agent-policy-kit)'s `ReadOnlyConnector`.

> Ships a `fake` backend (offline, in-memory); real backends are added through
> the registration extension point below.

## Configure (`filepeek.toml`)

```toml
[backend]
type = "fake"
# credentials for a real backend are NOT here — read them from the environment

[folders]              # the allowlist: folder -> permitted operations (read-only)
docs     = ["list", "read", "search", "download"]
"shared" = ["list", "read"]

[audit]
path = "filepeek-audit.log"
```

Unknown folders and any write operation are refused (fail-closed).

## Use

```bash
filepeek --config filepeek.toml folders
filepeek --config filepeek.toml list --folder docs
filepeek --config filepeek.toml read --folder docs --id 42
filepeek --config filepeek.toml search --folder docs --query report
```

```python
from filepeek.core import Config, FileStoreConnector
conn = FileStoreConnector.from_config(Config.load("filepeek.toml"))
conn.read("docs", file_id="42")   # policed, audited, secret-masked
```

## Add a backend

Implement `filepeek.core.backends.FileStoreBackend` (`list_folders`, `list_files`,
`read_file`, `search`, `download`) and `register("name", YourBackend)`.
Scope, audit and masking are handled by the connector.

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
