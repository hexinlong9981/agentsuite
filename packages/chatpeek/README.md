# chatpeek

A **read-only chat connector** for AI agents — `list` / `read` / `search` /
`download` over a pluggable chat backend, gated by a **fail-closed channel
allowlist**, with every call **audited** and results **secret-masked**. Read-only
by design (no post / edit / delete). Built on
[`agent-policy-kit`](../agent-policy-kit)'s `ReadOnlyConnector`.

> Ships a `fake` backend (offline); real backends plug in through the extension
> point (see "Add a backend").

## Configure (`chatpeek.toml`)

```toml
[backend]
type = "fake"
# a real backend would read credentials from the environment / a secret store, never here

[channels]             # the allowlist: channel -> permitted operations (read-only)
general = ["list", "read", "search", "download"]
random  = ["list", "read"]

[audit]
path = "chatpeek-audit.log"
```

Unknown channels and any write operation are refused (fail-closed).

## Use

```bash
chatpeek --config chatpeek.toml channels
chatpeek --config chatpeek.toml list --channel general
chatpeek --config chatpeek.toml read --channel general --id 42
chatpeek --config chatpeek.toml search --channel general --query invoice
```

```python
from chatpeek.core import Config, ChatConnector
conn = ChatConnector.from_config(Config.load("chatpeek.toml"))
conn.read("general", message_id="42")   # policed, audited, secret-masked
```

## Add a backend

Implement `chatpeek.core.backends.ChatBackend` (`list_channels`, `list_messages`,
`read_message`, `search`, `download_file`) and `register("name", YourBackend)`
in `chatpeek/core/backends/__init__.py`. Scope, audit and masking are handled by
the connector.

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
