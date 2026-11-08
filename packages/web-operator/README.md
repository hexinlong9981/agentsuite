# web-operator

**Policy-gated browser automation.** Every operation is checked against a
`host × operation-category` matrix (fail-closed). State-changing operations
(category **mutate**, e.g. a form submit) additionally require a **preview that a
human approves** before they run. The browser backend is pluggable, and every
operation and denial is audited.

> Ships a `FakeBackend` (in-memory, for demos/tests); a
> Playwright backend is an optional extra for real pages.

## Operation categories

| Category | Example operations | Gate |
|---|---|---|
| `navigate` | navigate | matrix |
| `read` | read, get_elements, screenshot | matrix |
| `download` | download | matrix |
| `interact` | fill, select, click | matrix |
| `mutate` | submit (confirm / send) | matrix **and** human approval (preview → approve → run) |

Unknown host → refused. Category not listed for the host → refused. `mutate`
listed but not approved at run time → refused (nothing reaches the backend).

## Configure (`web_operator.toml`)

```toml
[audit]
path = "web-operator-audit.log"

[hosts."app.example.com"]
allow = ["navigate", "read", "interact", "mutate", "download"]

[hosts."*.readonly.example"]
allow = ["navigate", "read"]
```

## Use

```python
from web_operator.core import Operator, HostPolicy, Config, FakeBackend, Callback

cfg = Config.load("web_operator.toml")

def approve(host, operation, preview):
    print("APPROVE?", preview)
    return input("y/n> ").strip().lower() == "y"

op = Operator(backend=FakeBackend(), policy=cfg.policy,
              approval=Callback(approve), audit=cfg.audit)

op.navigate("app.example.com", "https://app.example.com/orders")
op.read("app.example.com")
op.fill("app.example.com", "#qty", "2")
op.submit("app.example.com", "#order-form")   # previews, asks approve(), then runs
```

Inspection CLI:

```bash
weboperator --config web_operator.toml hosts
weboperator --config web_operator.toml check --host app.example.com --operation submit
```

## Add a backend

Implement `web_operator.core.backends.BrowserBackend` (`dispatch`, and
`describe` for mutate previews). The policy matrix, approval and audit are
provided by the `Operator`.

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
