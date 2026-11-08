# agentsuite — architecture & design policy

The authoritative design document for the suite. It states the design policy every
package follows, the uniform safety model, and the package catalogue.

## Mission

A **generic, business-agnostic toolkit** that lets AI agents work on real systems
**safely and simply**. It spans three domains:

1. **Operate real systems** — read-or-write access to source, databases, commands,
   files, metrics, the web, mailboxes, chat, file stores, issue trackers and
   interactive terminals, each behind one safety contract.
2. **The agent's own operation** — the knowledge an agent keeps and grows, the
   executable procedures it runs, the principles it works by, the conventions for
   authoring its skills, run observability, and MCP serving.
3. **Produce deliverables** — accessible, verifiable HTML report sites.

Each tool absorbs the repetitive, error-prone orchestration an agent would
otherwise do by hand, behind a few high-level calls, and enforces one uniform
safety contract.

## Design policy (non-negotiable)

1. **No business content.** No company / customer / product names, internal
   hostnames, IP addresses, internal filesystem paths, ticket IDs, or real source
   filenames — in code, config, tests, or docs. Everything is generic.
2. **No secrets in the repo.** Credentials, keys and tokens are never committed
   and never set as defaults. Configuration only *references* an external secret
   store; the values live outside the repo and are never returned by an API,
   printed, logged, or placed on a command line.
3. **Generic and reusable.** Everything must be useful to anyone, not tied to one
   organization or deployment.
4. **Fail-closed.** Anything not explicitly allowed is refused. Read-only by
   default; every write / mutating / external action is opt-in and gated.
5. **Human-gated where it matters.** Sending, submitting, approving, and any
   irreversible action are their own steps; nothing self-approves.
6. **Audited.** Every action, decision and denial is recorded — never the values.
7. **Polyglot authoring, one contract.** Where a tool offers more than one
   authoring surface (for example YAML or Java procedures), all surfaces compile
   to the same representation and obey the same checks and engine.

These rules are checked before anything is published (`scripts/pre_publish_scan.py`
for patterned leaks, plus a human name-review).

## The uniform capability model (read / write / allowlists)

Every tool expresses *what it may do* as a policy, using the same model:

- **Operations are classified read vs write.**
- **Capability is explicit per target.** Read-only by default; a write / mutating
  capability is opt-in.
- **Fine-grained allowlists decide exactly what is permitted** — for example which
  programs + argument patterns (`cmdgate`), which SQL statement shapes and a cost
  ceiling via EXPLAIN (`selectgate`), which paths and where they route
  (`remote-file-sync`), which `host × operation` (`web-operator`), which
  repositories / refs (`gitborrow`), which `resource × operation` (connectors),
  which commands / input (`term-operator`).
- **Write / mutate operations must match an allowlist** and may additionally
  require a **preview → human approval** step.
- **Unlisted ⇒ refused.**

Mechanically this is the shared `agent_policy_kit.ScopePolicy` (a fail-closed
`resource × operation` matrix with a read/write split) plus each tool's own
allowlist, centralised in `agent-policy-kit`.

## Simplify the agent (absorb the busywork)

Each tool exposes a few **intent-level** operations that internalise the common
multi-step work, so the agent calls one function instead of orchestrating many
calls and tracking state — for example `gitborrow`'s analysis API hides
*fetch → grep → read → cite → release*; `selectgate`'s plan-gate hides
*EXPLAIN → judge cost → run*; connectors expose `list` / `read` / `search` /
`download` over any provider; `procflow` runs a whole procedure from one entry
point. New tools follow this: intent-level calls that return structured,
citation-bearing, secret-masked results — not thin one-to-one RPC wrappers.

## Shared core — `agent-policy-kit`

Provides the governance every package builds on: `ScopePolicy` (fail-closed
`resource × operation`, read/write), `AuditLog`, `SecretMasker`, path containment
(`contained_join` / `safe_name` / `is_under`), a common exception base, a CLI
exit-code helper, the `ReadOnlyConnector` base, credential isolation
(`CredentialStore` → a handle that hands values only to a subprocess environment
and never returns or logs them), a human `Approval` hook (`DenyAll` safe default /
`AllowAll` / `Callback`), a bastion / jump-host SSH command builder, and OTP
providers (TOTP / callback / command) for bastion and 2FA prompts.

## Package catalogue

Capability legend: **R** read-only · **R+W** write opt-in (allowlist + approval).

### Core

| Package | Capability | Notes |
|---|---|---|
| `agent-policy-kit` | — | the shared governance core every package depends on |

### Operate real systems

| Package | Domain | Capability | Backends |
|---|---|---|---|
| `gitborrow` | source / VCS | R (no push) | git mirrors & remotes; disposable checkouts; secret-filtered; high-level analysis API |
| `selectgate` | database | R + write-mode (opt-in) | SQLite built-in; PostgreSQL/MySQL drivers with dialect plan-gates + timeout |
| `cmdgate` | commands | R default, R+W via allowlist | local; SSH (bastion / jump-host); irreversible-block |
| `remote-file-sync` | files (pull) | R (source read-only) | local; SSH (bastion / jump-host) |
| `metrics-fetcher` | metrics | R | synthetic, CSV, REST/JSON; optional cloudwatch |
| `web-operator` | browser | R + mutate (approval) | fake; Playwright optional |
| `mailpeek` | email | R | IMAP built-in + fake; optional MAPI |
| `chatpeek` | chat | R + post (approval) | fake (API client pluggable) |
| `filepeek` | file store | R + upload (approval) | fake (API client pluggable) |
| `issuepeek` | issues | R + create (approval) | fake (API client pluggable) |
| `term-operator` | interactive terminal | R + W (allowlist + approval) | fake + local_pty + ssh (bastion + OTP) + GUI-terminal driver |

### The agent's own operation

| Package | Domain | Notes |
|---|---|---|
| `knowledgebase` | reusable facts | one file per fact; index; grow-via-review (inbox → promote); freshness; consistency check |
| `procflow` | executable procedures (SOPs) | YAML **or** Java authoring → one IR; code/human/LLM/agent steps; human gates; evidence capture; inheritance / interfaces / calls; scheduling & unattended runs; self-improvement loop with guard-railed autonomous revision (safety-net + rollback, never pushes); headless worker; metrics ingestion; MCP serving |
| `playbook` | operating principles | principles as structured data + a rendered guide + a self-check/audit |
| `skillkit` | skill authoring | definition schema + consistency linter + scaffolder + templates |
| `agentflow` | observability | append-only run steps (JSONL), secrets masked; text/HTML viewer |
| `mcp-runtime` | runtime | host tools behind one MCP server: fail-closed tool manifest, audited dispatch, no credential-returning tools |

### Produce deliverables

| Package | Domain | Notes |
|---|---|---|
| `reportsite` | report sites | accessible, data-separated HTML sites + portable bundles; fail-closed completion gate (structure, secret-leak, accessibility/contrast, data integrity, links); light/dark themes; diagrams; presets; Markdown conversion |

## Excluded on purpose

Anything business-specific: the actual procedures, knowledge, routing registries,
schedules, report content, themes and credentials are the *user's* data, supplied
at run time, never in this repo. Product-specific admin UIs have no generic value
and are out of scope.
