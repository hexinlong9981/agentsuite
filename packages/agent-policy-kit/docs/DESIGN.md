# Design note

Several safe-integration tools repeat the same governance pattern: a fail-closed
scope, an audit trail, secret masking, and a read-only access surface. This kit
extracts that pattern so each integration is a thin instance rather than a
re-implementation. It is the business-agnostic core shared by read-only
connectors (chat / file store / mailbox / issue tracker), a metrics fetcher, and
policy-gated browser automation.

## Pieces and how they compose

```
ReadOnlyConnector.operate(resource, op)
    -> ScopePolicy.check(resource, op)      # fail-closed; read-only by default
    -> AuditLog.allow / .deny(reason)       # every decision recorded
    -> provider._dispatch(...)              # reads only
    -> SecretMasker.mask_obj(result)        # before returning to the caller
```

- **ScopePolicy** — a resource × operation matrix. Operations are split into read
  and write kinds; a read-only policy refuses writes regardless of the matrix.
  Unknown resources are refused unless `allow_unlisted` is set.
- **SecretMasker** — best-effort, defense-in-depth masking of text and structured
  data; redacts values whose key names a secret.
- **AuditLog** — JSON lines; denials carry the reason.
- **ReadOnlyConnector** — the composition above; a provider only writes
  `_dispatch` for read operations.
