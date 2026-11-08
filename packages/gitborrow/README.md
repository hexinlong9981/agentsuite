# gitborrow

Borrow a git repository into a **disposable, read-only, agent-safe** checkout,
reference it, analyze it, then return (discard) it. A thin policy wrapper around
`git` for the "read the code, don't keep it, don't push it, don't leak secrets"
use case — especially when the reader is an **AI coding agent**.

> Core + CLI + **high-level source-analysis API**.

## Why

A plain `git clone` leaves source lying around, keeps a pushable `origin`,
exposes any secrets in the tree, and tends to hand fetch credentials to whoever
runs it. gitborrow makes those four things impossible by construction.

In addition, it provides a **high-level, citation-bearing source-analysis API**
that absorbs repetitive grep/find/cat cycles into structured calls, keeping
ephemeral checkouts transparent to agents.

## The four guarantees (pillars)

| Pillar | Guarantee | Default | Configurable |
|---|---|---|---|
| ① Containment / ephemeral | checkouts live only under an ephemeral root (tmpfs); protected roots are refused (fail-closed) | tmpfs | roots, TTL |
| ② Push guard | no writes back to a remote; git subcommands/options allowlisted; option-injection blocked | **on** | **on/off** |
| ③ Secret filter | secrets in the tree are masked/removed before a consumer reads | on | action, rules, per-repo |
| ④ Credential isolation | fetch credentials go into the git subprocess only — never returned, printed, or logged | — | store: env/keyring/file |

Plus an audit log (denials included). The pillars are independent: turning the
push guard off does not disable the others.

## Source-analysis API (3 tiers)

All operations automatically manage checkout lifecycles, apply the secret filter,
and attach immutable citations (`repo@sha:path:line`).

- **Tier 1 (Primitives):**
  - `search`: pattern/regex search with surrounding context lines.
  - `read`: bounded slice of file content (never dumps unbounded full files).
  - `tree`: file hierarchy traversal with size and detected language.
  - `history`: commit logs for repo or specific paths.
  - `compare`: file-level diff between two revisions.
- **Tier 2 (Language-Aware):**
  - `definition`: locate classes, functions, methods, interfaces, types.
  - `references`: find usages, calls, and imports of a symbol.
  - `outline`: structural outline of a file without reading full contents.
- **Tier 3 (Composite):**
  - `call_trace`: trace caller or callee chains up to a configured depth.
  - `impact_scan`: scan all code paths accessing a target symbol, table, or data key.

## Install

```bash
pip install -e .            # core + analysis API + CLI
pip install -e '.[mcp]'     # also the MCP server
```

Requires Python ≥ 3.11 and `git`.

## Quickstart (CLI)

```bash
# gitborrow.toml
cat > gitborrow.toml <<'TOML'
[ephemeral]
dir = "/dev/shm/gitborrow"
[containment]
protected_roots = ["/path/to/your/workspace"]
[repositories]
allow_any = true           # or declare [repositories.<name>] entries (fail-closed)
[secret_filter]
enabled = true
action  = "mask"           # mask | delete | fail
[push_guard]
enabled = true
TOML

# Lifecycle
gitborrow --config gitborrow.toml fetch https://example.invalid/acme/widget.git --ref main
gitborrow --config gitborrow.toml list
gitborrow --config gitborrow.toml release widget
gitborrow --config gitborrow.toml purge --all

# Source analysis (transparently manages checkout lifecycle & citations)
gitborrow search widget "Calculator"
gitborrow read widget src/math.py --start 1 --end 20
gitborrow tree widget
gitborrow definition widget "Calculator"
gitborrow references widget "Calculator"
gitborrow outline widget src/math.py
gitborrow call-trace widget "compute_total" --direction callers
gitborrow impact-scan widget "orders" --kind table
```

Every result is pinned to a resolved SHA and quoted as `repo@sha:path:line`,
so evidence survives the checkout being discarded.

## Library

```python
from gitborrow import core
from gitborrow.core.analysis import AnalysisEngine

cfg = core.Config.load("gitborrow.toml")

# High-level analysis (lifecycle is managed automatically)
with AnalysisEngine(config=cfg) as engine:
    hits = engine.search("widget", "main", "process_payment")
    for hit in hits:
        print(hit.citation, hit.text)

    graph = engine.call_trace("widget", "main", "process_payment")
    print(f"Traced {len(graph.nodes)} callers")
```

## MCP

`gitborrow.mcp_server` exposes both lifecycle and analysis tools as MCP tools:
- Lifecycle: `fetch_repository`, `release_ephemeral`, `purge_ephemeral`, `list_ephemeral_checkouts`, `get_status`
- Analysis: `search_source`, `read_source_snippet`, `list_source_tree`, `find_definition`, `find_references`, `get_outline`, `trace_calls`, `impact_scan`

Tool results carry citations and never contain secrets; there is deliberately no
tool that reads credentials.
