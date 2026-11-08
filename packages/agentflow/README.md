# agentflow

Record an AI agent's run as **append-only steps** and render them. A `Recorder`
writes events (step start/end, notes, tool calls, errors) to
`<root>/<run_id>/events.jsonl`; event data is passed through the shared
`SecretMasker` first, so **the run log carries no secrets**. A `RunStore` lists
and loads runs (contained under the root), and `view` renders a run as text or a
self-contained HTML timeline.

> This is **observability**, not an access gate — it records what an agent did.

## Record

```python
from agentflow.core import Recorder

rec = Recorder("agentflow-runs", flow="investigate")
rec.note("picked up ticket")
with rec.step("fetch-source"):
    rec.tool("search", query="needle", hits=3)
rec.finish("ok")
```

## Inspect

```python
from agentflow.core import RunStore, render_text
meta, events = RunStore("agentflow-runs").load(rec.run_id)
print(render_text(meta, events))
```

```bash
agentflow --root agentflow-runs list
agentflow --root agentflow-runs show --run 20260101-120000-abcd1234
agentflow --root agentflow-runs show --run <id> --html --out run.html
```

## License

Apache-2.0. See the repository root [`LICENSE`](../../LICENSE).
