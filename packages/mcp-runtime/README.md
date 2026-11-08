# mcp-runtime

Host agentsuite tools behind **one MCP server**, with a **fail-closed manifest**
(only listed tools are exposed), **audited** dispatch, and **no credential-
returning tools** (secrets stay inside each tool's own credential store).

> The dispatch core is offline-testable; actually serving over
> MCP needs the optional `mcp` extra (`pip install mcp-runtime[mcp]`).

## Use

```python
from mcp_runtime.core import Config, Runtime, ToolRegistry, ToolSpec
from selectgate import core as sg

cfg = sg.Config.load("selectgate.toml")
reg = ToolRegistry()
reg.register(ToolSpec(
    "sql_query",
    lambda target, sql: sg.run_select(target, sql, config=cfg)[0].__dict__,
    "run a read-only SQL query",
))

rt = Runtime(reg, Config.load("mcp_runtime.toml"))   # manifest decides what is exposed
rt.enabled_tools()            # -> ["sql_query"] if listed in the manifest
rt.call("sql_query", target="reporting", sql="SELECT 1")

# serve over MCP (needs the 'mcp' extra):
# app = rt.build_mcp(); app.run()
```

```toml
# mcp_runtime.toml
[tools]
enabled = ["sql_query", "fetch_source"]   # fail-closed: anything not listed is refused

[audit]
path = "mcp-runtime-audit.log"
```

```bash
mcp-runtime --config mcp_runtime.toml manifest
```

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
