"""procflow — executable procedures (SOPs) for AI agents.

Author a procedure once (YAML or Java), compile it to a neutral IR, validate it,
and run it with code / human / LLM / agent steps under human-approval gates, with
full evidence capture, object-orientation (inheritance / interfaces / calls),
scheduling and unattended runs, and a guard-railed self-improvement loop.

Capabilities: definition schema, IR, compiler, static check (including extends / implements / call), the
7-layer runtime config and graph output; a non-daemon execution engine for code / human / choice steps with
loop, parallel/join, finally and rounds, run records (state.jsonl), a per-run lease lock, a pluggable tracer,
human decisions through an approval source, dry-run and run history; llm / agent steps behind pluggable
providers (fake / offload / session / disabled), target routing by granularity then layer, registries pinned
into each run, output validation with retry-then-escalate, a file-based work queue with atomic claims, replay
without provider calls and a resource catalogue; object-orientation at run time -- ``call`` child runs
(concrete ``sop`` or interface ``dispatch`` decided by code), child config narrowed toward the parent's,
interface pre/post contracts, typed exceptions (raise / catch), parent-child suspend propagation and
``resume --migrate``; improvement loop, smells detection, refactoring proposals, characterization testing,
autonomous revision with safety net & rollback, deterministic resume predicates, cron scheduling and
notifications; a Java authoring surface (annotations, builder, extractor, allowed-API static checking,
code generators yaml2java/ir2java/stub, and java-step execution). See docs/ARCHITECTURE.md Part A.
"""

from __future__ import annotations

from . import (agents, aistep, approval, catalog, children, config, contract, defschema, dryrun, engine, fakeprov, guard,
               harvest, history, java, javadefs, jsonschema_lite, launch, llm, lock, metrics, migrate, notify, offload, programs,
               records, refactor, registry, replay, resume_eval, revise, routing, rwhen, schedule, secret_filter, smells, tracer,
               workqueue)
from .approval import ApprovalSource, DecisionRefused, ScriptedApproval
from .engine import Engine, Refused, Usage, create_run, open_engine
from .check import Checker, Finding, run_check
from .compiler import compile_definition, compile_sop
from .errors import CompileError, ConfigError, DefinitionError, ProcflowError
from .graph import dot, mermaid, render
from .interfaces import load_interfaces
from .ir import IR, IRCall, IRInterface, IRNode, IRSop, IRTransition
from .model import Definition, InterfaceSpec, Step, Transition
from .repo import Repo
from .secret_filter import FilterResult, PushGuard, SourceCodeFilter

__version__ = "0.0.1"

__all__ = [
    "Checker", "Finding", "run_check", "compile_definition", "compile_sop", "Repo",
    "Definition", "Step", "Transition", "InterfaceSpec", "IR", "IRSop", "IRInterface", "IRNode",
    "IRTransition", "IRCall", "mermaid", "dot", "render", "load_interfaces", "config", "defschema", "guard",
    "approval", "dryrun", "engine", "history", "lock", "programs", "records", "tracer", "agents", "aistep", "catalog",
    "fakeprov", "jsonschema_lite", "llm", "offload", "registry", "replay", "routing", "workqueue", "children", "contract",
    "migrate", "metrics", "smells", "harvest", "refactor", "revise", "rwhen", "resume_eval", "notify", "schedule", "launch",
    "java", "javadefs", "secret_filter", "PushGuard", "SourceCodeFilter", "FilterResult",
    "ApprovalSource", "DecisionRefused", "ScriptedApproval", "Engine", "Refused", "Usage", "create_run",
    "open_engine",
    "ProcflowError", "DefinitionError", "CompileError", "ConfigError",
]
