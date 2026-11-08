# procflow

Executable procedures (SOPs) for AI agents. Author a procedure once — in YAML or
in Java — compile it to a neutral intermediate representation (IR), validate it,
and run it under one safety contract.

- **Steps**: `code` (allow-listed program lines, argv/no-shell), `human`
  (waits for a recorded approval — never self-approved), `llm` / `agent`
  (schema-validated output, retry-then-escalate).
- **Object-orientation**: `extends` (single-inheritance, LSP-checked overrides),
  `implements` interfaces (pre/post/safety contracts), and `call` (child runs
  with depth limits, narrowed config, typed `catch`).
- **Engine**: layered runtime config with caps/locks, branching/loop/parallel,
  suspend/resume/cancel/migrate, run records with full evidence capture.
- **Scheduling & unattended** runs (disabled by default), work-queue, resume
  conditions.
- **Self-improvement loop**: harvest/draft/propose, and guard-railed autonomous
  revision with a safety-net (characterization tests) and auto-rollback — commits
  locally only, never pushes; safety/human-gate/scope changes go to proposals for
  a human.
- **Java authoring surface** (optional): annotations + builder → extractor → the
  same IR, with an allowed-API rule enforced in the build.

Built on `agent-policy-kit` (approval gates, audit, secret masking, scope, path
containment, credential isolation). See
[`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) Part A.

## Runtime: worker, metrics, MCP

- **Headless Worker**: `procflow worker --agent <name>` runs unattended work-queue polling and ticket processing. Strictly enforces safety line-of-defense: human approval gates and interactive session capabilities (`bash`, `web`, `mcp`, `final_judgement`) are refused and skipped. Enforces concurrency caps, per-run cost limits, and daily expenditure ceilings.
- **Metrics Ingestion Adapter**: `procflow ingest` extracts execution metrics (run duration, distinct steps, failure counts, timestamps, status flags) from `run.json` and `state.jsonl` into an idempotent SQLite metrics store using SHA-256 fingerprinting. Guaranteed privacy: zero raw outputs, user inputs, prompt texts, or credentials are ever ingested. Query via `procflow ingest --query [--sop <ID>] [--status <status>]`.
- **MCP Serving**: `procflow mcp-serve` provides a Model Context Protocol stdio server exposing procflow operations (`procflow_check`, `procflow_compile`, `procflow_status`, `procflow_stats`, `procflow_smells`, `procflow_queue_list`, `procflow_eval_resume`) under a strict fail-closed manifest. Zero credential-returning tools: credentials remain completely isolated and all outputs are automatically sanitized via `SecretMasker`.
- **Configurable Push Guard**: `PushGuard` enables/disables remote git push blocking via environment variable (`PROCFLOW_PUSH_GUARD_ENABLED=1/0`) or API (`guard.set_enabled(False)`). Fail-safe by default (pushing blocked).
- **Source Code & Tree Sanitization**: `SourceCodeFilter` (`procflow filter-source <path> [--dry-run]`) scans downloaded repositories and workspace directories to automatically block sensitive files (`.env`, `*.key`, `*.pem`, `credentials.json`) and redact credentials/tokens from source contents before AI consumption.

## Java authoring

- **Java Authoring & Codegen**: `procflow java yaml2java` converts YAML procedure definitions into type-safe Java classes with `@Procedure`, `@Step`, and `ProcedureBuilder` flow configuration. `procflow java ir2java` generates typed Java interfaces (`@ProcedureInterface`) with nested `In` and `Out` records from YAML interface contracts. `procflow java stub` generates abstract Java base classes (`@ProcedureStub`) from abstract procedures.
- **Allowed-API Static Enforcement**: `procflow java check-api` inspects Java source files and trees for disallowed standard library packages (networking `java.net`, reflection `java.lang.reflect`, direct file write `java.io.FileWriter`, `Runtime.getRuntime()`, `ProcessBuilder`) to guarantee that procedure definitions remain pure and side-effect-free.
- **AST/Regex Extraction**: `procflow java extract` parses Java source code containing `@Procedure`, `@Step`, and builder method calls (`b.step`, `b.start`, `b.end`, `b.transition`, `guard`), translating them directly into neutral procflow definition documents that compile to the exact same IR and pass the exact same checks.
- **Java Step Execution**: `procflow` executes code steps via `java-step <StepClassName>` using an allow-listed, argv-only runner (`procflow.steps.runtime.StepMain`) under the configured JDK (`PROCFLOW_JAVA_HOME` / JDK 17+) with isolated process environments and no shell invocation.


## Improvement loop

- **Metrics & Smells**: `procflow stats` aggregates step execution metrics, duration percentiles, failure rates, and approval bottlenecks from `state.jsonl` and `run.json`. `procflow smells` evaluates static design smells (long action text, huge SOPs, deeply nested conditionals) and dynamic smells (high failure rate, p50 duration spikes, human approval bottlenecks).
- **Refactoring & Harvesting**: `procflow refactor` maps detected smells into concrete non-breaking proposals (`INTRODUCE_TIMEOUT`, `ADD_PRECONDITION`, `EXTRACT_SOP`, `SIMPLIFY_APPROVAL`). `procflow harvest` clusters repeated step sequences from execution traces into valid draft SOP YAML specifications.
- **Autonomous Revision & GitOps Safety Net**: `procflow revise` applies YAML revisions on isolated local feature branches (`revise/<id>_<ts>`). Before commit, `judge.py` evaluates a strict pre-commit verification gate (compilation, structural IR check, dry-run path simulation, output contract immutability, and preservation of human approval lines of defense). Any failure triggers an automatic rollback leaving the working tree clean.
- **Deterministic Resume Predicates**: `procflow eval-resume` evaluates deterministic predicate conditions (`exists`, `file_not_empty`, `time_elapsed`, `backoff`, `approval`, `var_eq`) on suspended runs without side effects, auditing decisions to `resume_eval.jsonl`.
- **Cron Scheduling & Unified Tick**: `procflow tick` runs a periodic scheduler evaluation tick, parsing 5-field cron specifications from `schedules.yaml` and evaluating suspended runs for auto-resumption.
- **Audited Notifications**: `procflow notify` records lifecycle events, scheduler alerts, and auto-resume triggers to an append-only log (`_notify.jsonl`) with automated secret masking (`SecretMasker`).


## Object-orientation at run time

- **`call`** starts a real child run (`<parent>~<step>-<n>`, its own `state.jsonl` / IR / config snapshot / evidence, linked by
  `parent` / `call_path` / `depth` / `depth_limit` / `dispatch`; the parent keeps `calls.json`). The callee is a concrete `sop:` or an
  `interface:` whose `dispatch` predicates (guard expressions, first true wins) then `default_impl` pick it -- decided by code,
  never by an LLM; none left raises `NoImplementation`. Depth beyond `min(limits.max_depth, call.max_depth, parent's remaining)`
  raises `LimitReached`. Interface input types / `pre` and output types / `post` (`contract.py`) failing raise `SystemError`.
- **Child config** is resolved from the child's own layers, then only narrowed toward the parent's resolved values
  (`config.narrow_for_child`: limits / retry / timeouts / budgets rounded down, step kinds and ai allow-lists intersected,
  `with_config` narrowing only), with provenance in the child's `config.snapshot.json` (`call.narrowed`) and a `call.narrowed`
  note in its log. The parent's run-level overrides and ai target choices do not reach it.
- **Exceptions**: a step raises a declared type (`submit --result error --raise <Type>`, or a code step's output `__raise__`);
  an undeclared type becomes `SystemError`. A call's `catch: {<Type>: <step>}` takes it, else `task_error_target`, else the run
  suspends (`limit`). A failed run records `raised` (run.json and the `run_end` event); the parent's call receives it.
- **Suspend / resume**: a child suspending suspends the parent (reason `child`); a suspended parent suspends its running
  children. Resume and cancel go through the **root** run only (children resume deepest first); tickets surface on the child run
  and `submit` / `next` take its id, the parents then advance by themselves. `show <run> --tree` prints the call tree.
- **`resume --migrate`** moves a suspended run (and its active children) to the current definition only when the new one passes
  check, keeps the same major (and implemented interface majors), still has the nodes / loops the run stands on with the same
  kind, and can receive every recorded variable / output / input. The old IR stays as `ir.<n>.json`; `run.json` gets `migrations`
  and the log a `migrate` event.
- **extends at run time**: a hook nobody overrode is skipped as a no-op; `super: before|after` steps run as `<id>-super`.

## AI steps, routing and queue

- **Registries** (`<config dir>/data_classes.yaml`, `llm_services.yaml`, `agents.yaml`, `llm_routing.yaml`) are validated at
  load, and pinned into each run (`registry.snapshot.json`). A built-in `session` target always exists (a work ticket for a worker).
- **Routing** picks a target by granularity (step → procedure default → routing table by `needs` / task class → built-in), then
  layer; candidates are filtered by allow-lists, data class, capabilities, model and (unattended) `ai.unattended_allowed`.
  A `local_only` data class is never routed outside: with no local target the step goes to a person. `procflow config show <ID> --steps`.
- **Providers**: `fake` (scripted; only with `PROCFLOW_ALLOW_FAKE_PROVIDERS=1`, script via `PROCFLOW_FAKE_SCRIPT` or the entry's
  `script`), `offload` (an interface; install a client with `procflow.offload.set_client`; none ships), `session` (queued ticket), `disabled`.
- **AI steps**: the output must match `io.output_schema`, the declared outputs, an optional rule program `_impl/<ID>/steps/<step>.py`
  and the agent's `checks`; rejections are fed back (`retry.llm`) and then escalated to a person. `budget.max_calls_per_run` /
  `budget.max_cost_per_run` suspend with `limit`. Per-attempt records: `<round>/<step>/ai-attempt-<n>.json` (masked).
- **Queue**: `procflow queue [--claim --ticket T --worker W | --release]` (atomic claim, lease, only the claimant submits).
- **Replay**: `procflow replay <run>|--all [--sop ID] [--current]` re-verifies transitions and recorded outputs without calling providers.
- **Catalogue**: `procflow catalog build|list|show|impact|check` (tool source: JSON file named by `PROCFLOW_CATALOG_SOURCES`).
- **Scaffold**: `procflow scaffold <ID> [--write]` emits `_impl/<ID>/` prompt / schema / rule templates (never overwrites).
