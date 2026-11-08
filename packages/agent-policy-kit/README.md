# agent-policy-kit

Small, dependency-free building blocks for giving an AI agent **safe, read-only
access** to a system — and a base class that turns "a connector to X" into a
thin, policed, audited, secret-masked instance.

> The shared foundation for read-only connectors (chat, file store, mailbox,
> issue tracker, metrics) and for policy-gated browser automation.

## Pieces

| Piece | What it gives you |
|---|---|
| `ScopePolicy` | fail-closed `resource × operation` matrix; **read-only by default** (write ops refused unless `read_only=False` *and* listed); glob resources; `allow_unlisted` opt-in |
| `SecretMasker` | mask secrets in text and in structured results (recurses dict/list; redacts secret-named keys); built-in + custom rules |
| `AuditLog` | JSON-lines log of allows and denials (with the deny reason) |
| `ReadOnlyConnector` | base class: `operate()` checks scope → audits → calls your `_dispatch()` (reads only) → masks the result |
| `KitConfig` | assemble the three from TOML/dict |

## Build a read-only connector

```python
from agent_policy_kit import ReadOnlyConnector, ScopePolicy, SecretMasker, AuditLog

class MailboxConnector(ReadOnlyConnector):
    def _dispatch(self, resource, operation, **p):
        if operation == "list":   return self._api.list_folders(resource)
        if operation == "read":   return self._api.read_message(resource, p["id"])
        if operation == "search": return self._api.search(resource, p["query"])
        raise ValueError(operation)

conn = MailboxConnector(
    scope=ScopePolicy({"inbox": {"list", "read", "search"}}),   # read-only, fail-closed
    audit=AuditLog("mailbox-audit.log"),
    masker=SecretMasker(),
    name="mailbox",
)
conn.read("inbox", id="42")        # policed, audited, masked
conn.operate("inbox", "delete")    # -> OperationNotAllowed (write under read-only)
conn.read("drafts", id="1")        # -> ScopeDenied (resource not in scope)
```

## Configure from TOML

```toml
[scope]
read_only = true
allow_unlisted = false
[scope.resources]
"inbox" = ["list", "read", "search"]
"files/*" = ["list", "read", "download"]

[secret_filter]
builtin = true

[audit]
path = "kit-audit.log"
```

```python
from agent_policy_kit import KitConfig
cfg = KitConfig.load("kit.toml")   # cfg.scope / cfg.masker / cfg.audit
```

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
