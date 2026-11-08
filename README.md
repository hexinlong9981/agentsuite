# agentsuite

A suite of small, **safe-by-default, generic tools for AI agents** — to operate
real systems, to keep and grow their own knowledge and procedures, and to produce
readable deliverables. Every tool is focused and independently installable, and
they all share one governance core so the same safety contract applies everywhere.

> Monorepo. `packages/agent-policy-kit` is the shared core (fail-closed scope,
> approval gates, audit, secret masking, path containment, credential isolation);
> every other package depends on it. Each package is its own distribution.

## Documentation

- [`docs/INSTALL_CLAUDE.md`](docs/INSTALL_CLAUDE.md): install agentsuite and use it with Claude Code (MCP server, CLIs, skills)
- [`docs/SECRETS.md`](docs/SECRETS.md): configure credentials and scopes (fail-closed)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): policy model and package catalogue

## Principles

- **No business content, no secrets.** Code, config, tests and docs never contain
  credentials, internal hostnames/paths, or customer/product names. Configuration
  only *references* an external secret store; values live outside the repo and are
  never returned, printed or logged.
- **Capability is explicit: read or write.** Read-only by default; writes,
  mutations and external actions are opt-in.
- **Fine-grained allowlists, fail-closed.** Exactly what is permitted — commands,
  SQL, paths, hosts, operations — is configured per target; anything unlisted is
  refused.
- **Human-gated where it matters.** Sending, approving and irreversible actions
  are their own steps; nothing self-approves.
- **Audited.** Every action and denial is logged — never the values.
- **Absorb the busywork.** Common multi-step work is implemented inside each tool
  and exposed as a few intent-level calls.

The full policy, the uniform model, and the package catalogue are in
**[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**.

## Packages

**Core**

| Package | What it does |
|---|---|
| [`agent-policy-kit`](packages/agent-policy-kit) | shared core: fail-closed scope matrix, audit log, secret masking, credential isolation, human approval hooks, bastion SSH builder & OTP providers |

**Operate real systems**

| Package | What it does |
|---|---|
| [`gitborrow`](packages/gitborrow) | fetch source into disposable, read-only, push-guarded, secret-filtered checkouts; high-level source analysis (search, definition, references, call_trace, impact_scan) |
| [`selectgate`](packages/selectgate) | SQL gateway: SELECT + EXPLAIN plan-gate + timeout (SQLite, PostgreSQL, MySQL/MariaDB); opt-in write-mode (statement allowlist + approval) |
| [`cmdgate`](packages/cmdgate) | run only allowlisted commands on allowlisted targets (local / SSH with bastion), read-only by default |
| [`remote-file-sync`](packages/remote-file-sync) | pull files into local destinations by policy: classify, contain, read-only source (local & bastion SSH) |
| [`metrics-fetcher`](packages/metrics-fetcher) | fetch time-series metrics from pluggable sources to CSV, with automatic granularity |
| [`web-operator`](packages/web-operator) | policy-gated browser automation; mutations need preview + human approval |
| [`mailpeek`](packages/mailpeek) | read-only mailbox connector; fail-closed folder allowlist |
| [`chatpeek`](packages/chatpeek) | chat connector; read + opt-in `post` (allowlist + approval) |
| [`filepeek`](packages/filepeek) | file-store connector; read + opt-in `upload` (allowlist + approval) |
| [`issuepeek`](packages/issuepeek) | issue-tracker connector; read + opt-in `create` (allowlist + approval) |
| [`term-operator`](packages/term-operator) | policy-gated interactive terminal; read=screen, send=command via allowlist + approval |

**The agent's own operation**

| Package | What it does |
|---|---|
| [`knowledgebase`](packages/knowledgebase) | a store of small reusable facts the agent reads and grows: recall by relevance, grow-via-review (inbox → promote), freshness tracking, consistency check |
| [`procflow`](packages/procflow) | executable procedures (SOPs): author in YAML or Java, compile to one IR, validate, and run with code / human / LLM / agent steps, human gates, evidence capture, object-orientation (inheritance / interfaces / calls), scheduling, a self-improvement loop with guard-railed autonomous revision, a headless worker, and MCP serving |
| [`playbook`](packages/playbook) | generic operating principles for an agent (delegation, commit hygiene, gating risky actions, deliverable style, progress reporting) as structured data + a rendered guide + a self-check |
| [`skillkit`](packages/skillkit) | conventions, a consistency linter, and a scaffolder for authoring agent skills and sub-agent definitions |
| [`agentflow`](packages/agentflow) | record an agent's run as append-only steps (JSONL), secrets masked; text/HTML viewer |
| [`mcp-runtime`](packages/mcp-runtime) | host these tools behind one MCP server: fail-closed tool manifest, audited dispatch, no credential-returning tools |

**Produce deliverables**

| Package | What it does |
|---|---|
| [`reportsite`](packages/reportsite) | generate accessible, data-separated HTML report sites (and portable bundles) and verify them with a fail-closed completion gate (structure, secret-leak, accessibility/contrast, data integrity, links) |

They share one pattern — **fail-closed scope, allowlists, audit, secret/credential
isolation, structured safe results** — centralised in `agent-policy-kit`.

## Layout

```
packages/<tool>/            # each: src/ tests/ README.md pyproject.toml
pyproject.toml              # uv workspace root (virtual) + unified test config
scripts/pre_publish_scan.py # zero-business-info / zero-secret audit
.github/workflows/ci.yml    # one matrix runs every package's tests
```

## Install (each package is its own distribution)

```bash
pip install selectgate        # just the SQL gateway
pip install procflow          # just the procedure engine
# ... agent-policy-kit is pulled in as a dependency
```

## Develop / test

```bash
# from the repo root — runs every package's tests against the shared core
python -m pytest
# zero-business-info and zero-secret audit
python3 scripts/pre_publish_scan.py
```

## License

[Apache License 2.0](LICENSE) — a permissive, OSI-approved open-source license.
Commercial use is permitted, there is no copyleft, and the license carries an
express patent grant. Retain the copyright and license notices and the
[`NOTICE`](NOTICE) file, and state any significant changes you make. Each package
carries the same license.
