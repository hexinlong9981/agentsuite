# mailpeek

A **read-only mailbox connector** for AI agents — `list` / `read` / `search` /
`download` over a pluggable mail backend, gated by a **fail-closed folder
allowlist**, with every call **audited** and results **secret-masked**. Read-only
by design (no send / delete / move). Built on
[`agent-policy-kit`](../agent-policy-kit)'s `ReadOnlyConnector`.

> Named `mailpeek` to avoid colliding with the Python stdlib `mailbox` module.
> Ships a `fake` backend (offline) and an `imap` backend
> (stdlib `imaplib`, read-only `BODY.PEEK`).

## Configure (`mailpeek.toml`)

```toml
[backend]
type = "imap"
host = "imap.example.com"
# credentials are NOT here — set MAILPEEK_IMAP_USER / MAILPEEK_IMAP_PASSWORD in the env

[folders]              # the allowlist: folder -> permitted operations (read-only)
INBOX     = ["list", "read", "search", "download"]
"Sent"    = ["list", "read"]

[audit]
path = "mailpeek-audit.log"
```

Unknown folders and any write operation are refused (fail-closed).

## Use

```bash
mailpeek --config mailpeek.toml folders
mailpeek --config mailpeek.toml list --folder INBOX
mailpeek --config mailpeek.toml read --folder INBOX --id 42
mailpeek --config mailpeek.toml search --folder INBOX --query invoice
```

```python
from mailpeek.core import Config, MailboxConnector
conn = MailboxConnector.from_config(Config.load("mailpeek.toml"))
conn.read("INBOX", message_id="42")   # policed, audited, secret-masked
```

## Add a backend

Implement `mailpeek.core.backends.MailBackend` (`list_folders`, `list_messages`,
`read_message`, `search`, `download_attachment`) and `register("name", YourBackend)`.
Scope, audit and masking are handled by the connector.

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
