"""``procflow`` command line.

Definition: check, compile, graph, config show.
Runs: run, next, submit, status, suspend, resume, cancel, runs, show, log, steps-json, dry-run.
AI steps: queue, replay, catalog, scaffold, and ``config show --steps`` (llm / agent targets).
Calls: ``submit --raise <Type>``, ``resume --migrate``, ``show --tree`` (the call tree); ``submit`` / ``next`` take a child run's
id (the parents advance by themselves) and a child is resumed / cancelled only through its root run.

Exit codes: 0 ok / ticket issued / END; 1 check, config, state or limit refusal (``queue --claim`` with nothing to
claim, ``replay`` with an unaccepted run, ``catalog check`` with a missing referenced resource); 2 usage or
parameter error; 4 suspended.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import sys
from pathlib import Path

import yaml
from agent_policy_kit import SecretMasker

from . import aistep as A
from . import catalog as CAT
from . import config as cfg
from . import dryrun as DR
from . import engine as E
from . import graph as G
from . import harvest as HV
from . import history as H
from . import ingest as IG
from . import java as J
from . import launch as LN
from . import lock as L
from . import mcp_server as MCPS
from . import metrics as M
from . import notify as NT
from . import records as R
from . import refactor as RF
from . import registry as REG
from . import replay as RP
from . import resume_eval as RE
from . import revise as RV
from . import routing as RT
from . import schedule as SC
from . import secret_filter as SF
from . import smells as SM
from . import worker as WK
from . import workqueue as WQ
from .approval import DecisionRefused, get_approval
from .check import Checker, run_check
from .compiler import compile_sop
from .errors import ProcflowError
from .repo import Repo
from .tracer import get_tracer

_MASK = SecretMasker()


def _common(p):
    p.add_argument("--sop-root", default=os.environ.get("PROCFLOW_ROOT", "."),
                   help="directory with procedure definitions (default: $PROCFLOW_ROOT or .)")
    p.add_argument("--interfaces-dir", default=None, help="override <root>/_interfaces")
    p.add_argument("--config-dir", default=None, help="override <root>/_config")


def build_parser():
    ap = argparse.ArgumentParser(prog="procflow", description="Executable procedures: check, compile, graph, config.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="static checks (all procedures, interfaces and config files, or the given ids)")
    _common(c)
    c.add_argument("ids", nargs="*")
    c.add_argument("--run", action="store_true", help="also refuse abstract procedures (may it be run directly?)")
    c.add_argument("--json", action="store_true", help="print findings as JSON")

    c = sub.add_parser("compile", help="compile one procedure to IR (refuses if the check fails)")
    _common(c)
    c.add_argument("id")
    c.add_argument("--out", default=None, help="write the IR JSON here instead of stdout")

    c = sub.add_parser("graph", help="draw a procedure (mermaid by default)")
    _common(c)
    c.add_argument("id")
    c.add_argument("--format", choices=["mermaid", "dot"], default="mermaid")

    c = sub.add_parser("config", help="runtime config")
    csub = c.add_subparsers(dest="config_cmd", required=True)
    s = csub.add_parser("show", help="show the resolved values and where each came from")
    _common(s)
    s.add_argument("id")
    s.add_argument("--env", default=None)
    s.add_argument("--config", default=None, help="L6 config file")
    s.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="L7 override (repeatable)")
    s.add_argument("--param", action="append", default=[], metavar="NAME=VALUE", help="input value (repeatable)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--steps", action="store_true",
                   help="show each llm / agent step's resolved execution target and fallback order instead")

    # ---- running procedures ---------------------------------------------------------
    def runs_common(p, repo_default="."):
        p.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS"),
                       help="where run records live (default: $PROCFLOW_RUNS, else the config key evidence.root)")
        p.add_argument("--sop-root", default=os.environ.get("PROCFLOW_ROOT", repo_default))
        p.add_argument("--impl-root", default=None, help="implementation root for code steps (default <sop-root>/_impl/<ID>)")
        p.add_argument("--skill-scripts-root", action="append", default=None,
                       help="extra allowed root for code programs (repeatable; env PROCFLOW_SKILL_ROOTS, os.pathsep-separated)")
        p.add_argument("--approval", default=os.environ.get("PROCFLOW_APPROVAL", "deny"),
                       help="human-decision source: deny (default) | prompt | file:<path>")
        p.add_argument("--tracer", default=os.environ.get("PROCFLOW_TRACER"), help="noop | fake | jsonl:<path>")
        p.add_argument("--json", action="store_true")

    c = sub.add_parser("run", help="start a run and advance it to the first ticket / the end")
    runs_common(c)
    c.add_argument("id")
    c.add_argument("--interfaces-dir", default=None)
    c.add_argument("--config-dir", default=None)
    c.add_argument("--env", default=None)
    c.add_argument("--config", default=None)
    c.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    c.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
    c.add_argument("--run-id", default=None)
    c.add_argument("--allow-same-input", action="store_true", help="skip the same-procedure-same-input guard")

    c = sub.add_parser("next", help="advance a run and show the next ticket")
    runs_common(c, None)
    c.add_argument("run_id")

    c = sub.add_parser("submit", help="record a step result / decision / label for the pending ticket")
    runs_common(c, None)
    c.add_argument("run_id")
    c.add_argument("--step", default=None)
    c.add_argument("--ticket", default=None)
    c.add_argument("--result", default="ok", choices=["ok", "error"])
    c.add_argument("--output", action="append", default=[], metavar="NAME=VALUE")
    c.add_argument("--outputs-file", default=None, help="JSON map of outputs")
    c.add_argument("--label", default=None)
    c.add_argument("--decision", default=None, help="human step: the claimed decision (the approval source must confirm it)")
    c.add_argument("--by", default=None)
    c.add_argument("--reason", default=None)
    c.add_argument("--raise", dest="raise_", default=None, metavar="TYPE",
                   help="with --result error: raise this declared exception type (an undeclared type becomes SystemError)")

    c = sub.add_parser("status", help="show the run's state (read-only, no lock)")
    runs_common(c, None)
    c.add_argument("run_id")

    c = sub.add_parser("suspend", help="suspend a run at a safe boundary")
    runs_common(c, None)
    c.add_argument("run_id")
    c.add_argument("--note", default="manual")

    c = sub.add_parser("resume", help="resume a suspended run (a child is resumed through its root run)")
    runs_common(c, None)
    c.add_argument("run_id")
    c.add_argument("--takeover", action="store_true", help="take over a stale lease")
    c.add_argument("--migrate", action="store_true",
                   help="move the run (and its child runs) to the current definition when that is safe, then resume")

    c = sub.add_parser("cancel", help="close a suspended run")
    runs_common(c, None)
    c.add_argument("run_id")
    c.add_argument("--reason", default="cancelled")

    c = sub.add_parser("runs", help="list runs from the index")
    runs_common(c, None)
    c.add_argument("--status", default=None)
    c.add_argument("--sop", default=None)
    c.add_argument("--reindex", action="store_true", help="rebuild the index from run.json files")

    for name, helptext in (("show", "run summary (read-only)"), ("log", "state log lines (read-only)"),
                           ("steps-json", "per-step results and evidence locations as JSON (read-only)")):
        c = sub.add_parser(name, help=helptext)
        runs_common(c, None)
        c.add_argument("run_id")
        if name == "log":
            c.add_argument("--tail", type=int, default=None)
        if name == "show":
            c.add_argument("--tree", action="store_true", help="print the call tree (the run and its child runs)")

    # ---- queue, replay, catalogue, scaffold ------------------------------------------
    c = sub.add_parser("queue", help="the work queue of session work tickets (list / --claim / --release)")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS"))
    c.add_argument("--state", choices=["pending", "claimed", "all"], default="pending")
    c.add_argument("--claim", action="store_true", help="atomically claim one pending ticket (the given one, else the oldest)")
    c.add_argument("--release", action="store_true", help="give a ticket you claimed back to the queue")
    c.add_argument("--ticket", default=None)
    c.add_argument("--run-id", default=None, help="narrow --ticket to one run")
    c.add_argument("--worker", default=None, help="worker name (default: $PROCFLOW_SESSION, else user@host)")
    c.add_argument("--lease", type=int, default=WQ.LEASE_SECONDS, help="claim lease in seconds")
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("replay", help="re-check runs against the definition without calling providers")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS"))
    c.add_argument("--sop-root", default=os.environ.get("PROCFLOW_ROOT", "."))
    c.add_argument("--config-dir", default=None)
    c.add_argument("run_id", nargs="?")
    c.add_argument("--all", action="store_true")
    c.add_argument("--sop", default=None, help="with --all: only runs of this procedure")
    c.add_argument("--current", action="store_true", help="use today's definition instead of the run's snapshot")
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("catalog", help="resource catalogue: build / list / show / impact / check")
    csub2 = c.add_subparsers(dest="catalog_cmd", required=True)
    for name in ("build", "list", "show", "impact", "check"):
        q = csub2.add_parser(name)
        _common(q)
        q.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS"))
        q.add_argument("--out", default=None, help="catalogue directory (default <runs-root>/_catalog)")
        q.add_argument("--json", action="store_true")
        if name in ("show", "impact"):
            q.add_argument("resource")
        if name == "list":
            q.add_argument("--type", default=None)

    c = sub.add_parser("scaffold", help="templates for a procedure's llm / agent steps under _impl/<ID>/")
    _common(c)
    c.add_argument("id")
    c.add_argument("--impl-root", default=None)
    c.add_argument("--write", action="store_true", help="write the files that do not exist yet (never overwrites)")
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("dry-run", help="enumerate reachable paths without creating a run")
    _common(c)
    c.add_argument("id")
    c.add_argument("--max-rounds", type=int, default=3)
    c.add_argument("--json", action="store_true")

    # ---- improvement loop, smells, revision, schedule --------------------------------
    c = sub.add_parser("stats", help="aggregate execution metrics across runs")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS", "runs"))
    c.add_argument("--sop", default=None, help="filter by procedure ID")
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("smells", help="detect architectural and runtime process smells")
    _common(c)
    c.add_argument("id")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS", "runs"))
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("refactor", help="generate refactoring proposals for a procedure")
    _common(c)
    c.add_argument("id")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS", "runs"))
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("harvest", help="synthesize draft procedure definitions from traces")
    c.add_argument("--traces-file", required=True, help="path to JSONL or Markdown traces")
    c.add_argument("--title", default="Draft Procedure")
    c.add_argument("--sop-id", default="SOP-DRAFT-001")
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("revise", help="apply verified revision with git safety net and rollback")
    c.add_argument("file", help="path to the target procedure YAML definition")
    c.add_argument("--revised-file", required=True, help="path to proposed revised definition")
    c.add_argument("--message", default=None, help="git commit message")
    c.add_argument("--no-branch", action="store_true", help="do not create feature branch")
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("tick", help="execute a single periodic scheduler evaluation tick")
    c.add_argument("--schedules-file", default=None, help="path to schedules.yaml")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS", "runs"))
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("eval-resume", help="evaluate resumption conditions for suspended runs")
    c.add_argument("run_id", nargs="?")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS", "runs"))
    c.add_argument("--workspace-root", default=None)
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("notify", help="read notification event log")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS", "runs"))
    c.add_argument("--kind", action="append", default=None, help="filter by event kind")
    c.add_argument("--sop", default=None, help="filter by procedure ID")
    c.add_argument("--limit", type=int, default=50)
    c.add_argument("--json", action="store_true")

    # Java authoring surface
    c = sub.add_parser("java", help="Java procedure authoring, codegen, allowed-API check, and extraction")
    j_sub = c.add_subparsers(dest="java_cmd", required=True)

    j_y2j = j_sub.add_parser("yaml2java", help="convert YAML procedure definition to Java class")
    j_y2j.add_argument("file", help="path to procedure YAML file")
    j_y2j.add_argument("--out", "-o", default=None, help="output .java file path (prints to stdout if omitted)")
    j_y2j.add_argument("--class-name", default=None)
    j_y2j.add_argument("--package", default="procflow.defs")

    j_i2j = j_sub.add_parser("ir2java", help="convert interface YAML definition to Java interface")
    j_i2j.add_argument("file", help="path to interface YAML file")
    j_i2j.add_argument("--out", "-o", default=None)
    j_i2j.add_argument("--class-name", default=None)
    j_i2j.add_argument("--package", default="procflow.defs.generated")

    j_st = j_sub.add_parser("stub", help="generate abstract Java stub from YAML procedure definition")
    j_st.add_argument("file", help="path to procedure YAML file")
    j_st.add_argument("--out", "-o", default=None)
    j_st.add_argument("--class-name", default=None)
    j_st.add_argument("--package", default="procflow.defs.generated")

    j_chk = j_sub.add_parser("check-api", help="statically check Java files for disallowed APIs")
    j_chk.add_argument("path", help="path to Java source file or directory")
    j_chk.add_argument("--json", action="store_true")

    j_ext = j_sub.add_parser("extract", help="extract procedure definition from Java source file or directory")
    j_ext.add_argument("path", help="path to Java source file or directory")
    j_ext.add_argument("--out", "-o", default=None)
    j_ext.add_argument("--json", action="store_true")

    # Headless worker, metrics ingestion, and MCP server
    c = sub.add_parser("worker", help="run headless worker polling unattended tickets")
    c.add_argument("--agent", required=True, help="worker agent name")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS", "runs"))
    c.add_argument("--caps", default=None, help="comma-separated agent capabilities")
    c.add_argument("--max-tickets", type=int, default=10)
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("ingest", help="ingest run execution metrics into SQLite store")
    c.add_argument("--runs-root", default=os.environ.get("PROCFLOW_RUNS", "runs"))
    c.add_argument("--db", default=None, help="path to SQLite metrics database")
    c.add_argument("--query", action="store_true", help="query ingested metrics")
    c.add_argument("--sop", default=None, help="filter by SOP ID for query")
    c.add_argument("--status", default=None, help="filter by status for query")
    c.add_argument("--json", action="store_true")

    c = sub.add_parser("mcp-serve", help="run MCP stdio server with fail-closed tool manifest")

    c = sub.add_parser("filter-source", help="scan and mask secrets in source files or directory trees")
    c.add_argument("path", help="path to source file or directory")
    c.add_argument("--dry-run", action="store_true", help="scan and detect without modifying files in-place")
    c.add_argument("--json", action="store_true")

    return ap


def _repo(a):
    return Repo(a.sop_root, a.interfaces_dir, a.config_dir)


def _cmd_check(a):
    repo = _repo(a)
    findings = run_check(repo, a.ids or None, run=a.run)
    if a.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2))
    else:
        for f in findings:
            print(f"[{f.severity}] {f.target}: {f.message}")
        print(f"{len(findings)} finding(s)" if findings else "OK: no findings")
    return 1 if findings else 0


def _cmd_compile(a):
    repo = _repo(a)
    errs = Checker(repo).check_sop(a.id)
    if errs:
        for m in errs:
            print(f"[error] {a.id}: {m}", file=sys.stderr)
        print("refusing to compile: the check failed", file=sys.stderr)
        return 1
    ir, _ = compile_sop(a.id, repo)
    text = json.dumps(ir.to_dict(), indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {a.out}")
    else:
        print(text)
    return 0


def _cmd_graph(a):
    ir, errs = compile_sop(a.id, _repo(a))
    if ir is None:
        print("; ".join(errs), file=sys.stderr)
        return 1
    sys.stdout.write(G.render(ir, a.format))
    return 0


def _cmd_config_show(a):
    repo = _repo(a)
    ir, errs = compile_sop(a.id, repo)
    if ir is None:
        print("; ".join(errs), file=sys.stderr)
        return 1
    try:
        res = cfg.resolve(ir, repo, env=a.env, config_file=a.config, sets=a.set, params=a.param)
    except cfg.ConfigError as x:
        for m in x.errors:
            print(f"[error] {m}", file=sys.stderr)
        return x.code
    if a.steps:
        reg, rerrs = REG.load(repo.config_dir)
        if rerrs:
            for m in rerrs:
                print(f"[error] registry: {m}", file=sys.stderr)
            return 1
        if a.json:
            print(json.dumps(_MASK.mask_obj(RT.routes(ir.to_dict(), res["values"], reg)), indent=2, ensure_ascii=False))
        else:
            print(RT.render_rows(a.id, RT.rows(ir.to_dict(), res["values"], reg)))
        return 0
    if a.json:
        print(json.dumps(cfg.mask_result(res), indent=2, ensure_ascii=False, default=str))
    else:
        print(cfg.render_table(res))
    for m in res["input_errors"]:
        print(f"[input] {m}", file=sys.stderr)
    return 2 if res["input_errors"] else 0


# ---- run commands ----------------------------------------------------------------------
def _skill_roots(a):
    roots = list(a.skill_scripts_root or [])
    env = os.environ.get("PROCFLOW_SKILL_ROOTS")
    if env and not roots:
        roots = [x for x in env.split(os.pathsep) if x]
    return roots or None


def _runs_root(a, default=None):
    return a.runs_root or default


def _engine(a, with_repo=False):
    if not a.runs_root:
        raise E.Usage("--runs-root (or $PROCFLOW_RUNS) is required for this command")
    repo = Repo(a.sop_root) if (with_repo and a.sop_root) else None
    return E.open_engine(a.runs_root, a.run_id, repo=repo, approval=get_approval(a.approval),
                         tracer=get_tracer(a.tracer), skill_roots=_skill_roots(a))


def _show_desc(d, as_json):
    d = _MASK.mask_obj(d)
    if as_json:
        print(json.dumps(d, indent=2, ensure_ascii=False, default=str))
        return
    st = d["status"]
    below = _child_tickets(d)
    nxt = d["ticket"]["type"] if d.get("ticket") else "CHILD" if below else \
        {"done": "END", "failed": "FAILED", "cancelled": "CANCELLED", "suspended": "SUSPENDED"}.get(st, "WAIT")
    print(f"PROCFLOW_RUN={d['run_id']}")
    print(f"PROCFLOW_STATUS={st}")
    print(f"PROCFLOW_NEXT={nxt}")
    for p in d.get("progress") or []:
        extra = " ".join(f"{k}={p[k]}" for k in ("stdout_bytes", "stderr_bytes", "evidence_dir", "decision", "why") if k in p)
        print(f"step {p['step']}: {p['result']}" + (" (no-op)" if p.get("noop") else "") + (" [finally]" if p.get("finally") else "")
              + (" " + extra if extra else ""))
    t = d.get("ticket")
    if t:
        print(f"ticket {t['ticket']} type={t['type']} step={t['step']} file={t.get('path')}")
        if t.get("candidates"):
            print("  candidates: " + ", ".join(str(c["label"]) for c in t["candidates"]))
        if t.get("outputs"):
            print("  outputs: " + ", ".join(t["outputs"]))
        for ln in t.get("lines") or []:
            print(f"  line: {ln['line']}  ({ln.get('why')})")
    for rid, t in below:
        print(f"PROCFLOW_CHILD={rid}")
        print(f"ticket {t['ticket']} type={t['type']} step={t['step']} file={t.get('path')}  "
              f"(submit to the child run id: procflow submit {rid} ...)")
    if d.get("raised"):
        print(f"raised: {d['raised']}")
    for m in d.get("migrated") or []:
        print(f"migrated: {m['run_id']} -> version {m['version']}")
    if d.get("queued"):
        print("queued: waiting for a worker (procflow queue --claim)")
    if d.get("suspend"):
        print(f"suspended: {d['suspend'].get('reason')}: {d['suspend'].get('note')}")
        root = (d.get("root") or {}).get("run_id")
        if d.get("parent") and root:
            print(f"this is a child run: resume the root run {root} (procflow resume {root}); a child cannot be resumed alone")
    if d.get("pending", 0) > 1:
        print(f"pending tickets: {d['pending']}")


def _child_tickets(d):
    """Pending tickets on child runs below a described run: [(run id, ticket)]."""
    out = []
    for k in d.get("children") or []:
        if k.get("ticket"):
            out.append((k["run_id"], k["ticket"]))
        out += _child_tickets(k)
    return out


def _finish_cmd(eng, d, a):
    _show_desc(d, a.json)
    return eng.exit_code(d)


def _cmd_run(a):
    repo = Repo(a.sop_root, a.interfaces_dir, a.config_dir)
    eng, d = E.create_run(repo, a.id, runs_root=a.runs_root, env=a.env, config_file=a.config, sets=a.set,
                          params=a.param, run_id=a.run_id, approval=get_approval(a.approval),
                          tracer=get_tracer(a.tracer), impl_root=a.impl_root, skill_roots=_skill_roots(a),
                          allow_same_input=a.allow_same_input)
    return _finish_cmd(eng, d, a)


def _cmd_next(a):
    eng = _engine(a)
    return _finish_cmd(eng, eng.cmd_next(), a)


def _parse_outputs(a):
    out = {}
    if a.outputs_file:
        d = json.loads(Path(a.outputs_file).read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise E.Usage("--outputs-file must hold a JSON map")
        out.update(d)
    for it in a.output:
        if "=" not in it:
            raise E.Usage("--output expects NAME=VALUE")
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = yaml.safe_load(v)
        except yaml.YAMLError:
            out[k.strip()] = v
    return out


def _cmd_submit(a):
    eng = _engine(a)
    d = eng.cmd_submit(step=a.step, ticket=a.ticket, result=a.result, outputs=_parse_outputs(a), label=a.label,
                       decision=a.decision, by=a.by, reason=a.reason, raise_=a.raise_)
    return _finish_cmd(eng, d, a)


def _cmd_status(a):
    eng = _engine(a)
    return _finish_cmd(eng, eng.describe(), a)


def _cmd_suspend(a):
    eng = _engine(a)
    return _finish_cmd(eng, eng.cmd_suspend(a.note), a)


def _cmd_resume(a):
    eng = _engine(a, with_repo=True)
    return _finish_cmd(eng, eng.cmd_resume(takeover=a.takeover, migrate=a.migrate), a)


def _cmd_cancel(a):
    eng = _engine(a)
    return _finish_cmd(eng, eng.cmd_cancel(a.reason), a)


def _cmd_runs(a):
    root = a.runs_root or "runs"
    if a.reindex:
        print(f"reindexed {R.reindex(root)} run(s)")
    rows = H.runs(root, a.status, a.sop)
    if a.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r['run_id']}  {r.get('sop')}  {r.get('status')}" + (f"({r['reason']})" if r.get("reason") else ""))
        print(f"{len(rows)} run(s)")
    return 0


def _rd(a):
    if not a.runs_root:
        raise E.Usage("--runs-root (or $PROCFLOW_RUNS) is required for this command")
    return R.find_run(a.runs_root, a.run_id)


def _cmd_show(a):
    if a.tree:
        tree = _MASK.mask_obj(R.call_tree(a.runs_root, _rd(a).run_id))
        if a.json:
            print(json.dumps(tree, indent=2, ensure_ascii=False, default=str))
        else:
            print("\n".join(H.tree_lines(tree)))
        return 0
    d = _MASK.mask_obj(H.show(_rd(a)))
    print(json.dumps(d, indent=2, ensure_ascii=False, default=str))
    return 0


def _cmd_log(a):
    for ln in H.log(_rd(a), a.tail):
        print(_MASK.mask_text(ln))
    return 0


def _cmd_steps_json(a):
    print(json.dumps(H.steps_json(_rd(a)), indent=2, ensure_ascii=False))
    return 0


def _cmd_dry_run(a):
    ir, errs = compile_sop(a.id, _repo(a))
    if ir is None:
        print("; ".join(errs), file=sys.stderr)
        return 1
    res = DR.enumerate_paths(ir.to_dict(), a.max_rounds)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        for i, p in enumerate(res["paths"], 1):
            print(f"[{i}] {p['path']}  => {p['terminal']}" + (f": {p['detail']}" if p["detail"] else ""))
        print(f"{res['count']} path(s)" + (" (truncated)" if res["truncated"] else ""))
    return 0


def _cmd_queue(a):
    if not a.runs_root:
        raise E.Usage("--runs-root (or $PROCFLOW_RUNS) is required for this command")
    q = WQ.WorkQueue(a.runs_root, lease=a.lease)
    worker = a.worker or L.owner_id()
    if a.claim and a.release:
        raise E.Usage("--claim and --release are exclusive")
    if a.claim:
        d = q.claim(worker, ticket=a.ticket, run_id=a.run_id)
        if d is None:
            print("nothing could be claimed (no pending ticket, or another worker took it)", file=sys.stderr)
            return 1
        base = Path(a.runs_root) / str(d.get("run"))
        d = dict(d, ticket_file=str(base / str(d.get("path"))), worker=WQ.safe_name(worker))
        if d.get("path"):
            t = R.read_json(base / d["path"], {}) or {}
            if t.get("prompt_file"):
                d["prompt_file"] = str(Path(d["ticket_file"]).parent / t["prompt_file"])
        d = _MASK.mask_obj(d)
        if a.json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
        else:
            print(f"PROCFLOW_CLAIMED={d['ticket']}")
            print(f"run {d['run_id']} step {d['step']} target {d.get('target')} worker {d['worker']}")
            print(f"ticket_file {d['ticket_file']}")
            if d.get("prompt_file"):
                print(f"prompt_file {d['prompt_file']}")
            print(f"lease until {d['claim']['expires']}; submit with: procflow submit {d['run_id']} --step {d['step']} "
                  f"--by {d['worker']} --output ...")
        return 0
    if a.release:
        if not a.ticket:
            raise E.Usage("--release needs --ticket")
        ok = q.release(worker, a.ticket, run_id=a.run_id)
        print("released" if ok else "not released (not claimed by this worker)")
        return 0 if ok else 1
    rows = q.list(a.state)
    if a.json:
        print(json.dumps(_MASK.mask_obj(rows), indent=2, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{r['state']:<8} {r['ticket']}  run {r['run_id']}  step {r['step']}"
                  + (f"  worker {r['worker']} until {r['expires']}" if r.get("worker") else ""))
        print(f"{len(rows)} ticket(s)")
    return 0


def _cmd_replay(a):
    if not a.runs_root:
        raise E.Usage("--runs-root (or $PROCFLOW_RUNS) is required for this command")
    repo = Repo(a.sop_root, config_dir=a.config_dir)
    if a.all:
        rows = RP.replay_all(a.runs_root, repo, a.sop, a.current)
    elif a.run_id:
        rows = [RP.replay_run(R.find_run(a.runs_root, a.run_id), repo, a.current)]
    else:
        raise E.Usage("give a run id or --all")
    if a.json:
        print(json.dumps(_MASK.mask_obj(rows), indent=2, ensure_ascii=False))
    else:
        print(_MASK.mask_text(RP.render(rows)))
    return 0 if RP.all_accepted(rows) else 1


def _catalog_dir(a):
    return a.out or str(Path(a.runs_root or "runs") / "_catalog")


def _cmd_catalog(a):
    repo = _repo(a)
    cmd = a.catalog_cmd
    if cmd == "build":
        cat = CAT.build(repo)
        diff = CAT.write(cat, _catalog_dir(a))
        print(json.dumps({"resources": len(cat["resources"]), "references": len(cat["references"]), "diff": diff,
                          "notes": cat["notes"]}, indent=2) if a.json else
              f"catalogued {len(cat['resources'])} resource(s), {len(cat['references'])} referenced"
              + (f"; changes: +{len(diff['added'])} -{len(diff['removed'])} ~{len(diff['changed'])}" if diff else ""))
        return 0
    cat = CAT.build(repo) if cmd == "check" else (CAT.load(_catalog_dir(a)) or CAT.build(repo))
    if cmd == "list":
        print(json.dumps(cat["resources"], indent=2) if a.json else CAT.render_list(cat, a.type))
        return 0
    if cmd == "show":
        r = cat["resources"].get(a.resource)
        if r is None:
            print(f"[refused] {a.resource} is not in the catalogue", file=sys.stderr)
            return 1
        print(json.dumps(dict(r, id=a.resource, referenced_by=CAT.impact(cat, a.resource)), indent=2, ensure_ascii=False))
        return 0
    if cmd == "impact":
        who = CAT.impact(cat, a.resource)
        print(json.dumps(who, indent=2) if a.json else "\n".join(f"{w['sop']}.{w['step']}" for w in who) + f"\n{len(who)} reference(s)")
        return 0
    errs, warns = CAT.check(cat)
    for m in errs:
        print(f"[error] {m}")
    for m in warns:
        print(f"[warning] {m}")
    print(f"{len(errs)} error(s), {len(warns)} warning(s)" if (errs or warns) else "OK: every referenced resource is available")
    return 1 if errs else 0


def _cmd_scaffold(a):
    repo = _repo(a)
    ir, errs = compile_sop(a.id, repo)
    if ir is None:
        print("; ".join(errs), file=sys.stderr)
        return 1
    files = A.scaffold(ir.to_dict())
    root = Path(a.impl_root) if a.impl_root else Path(repo.root) / "_impl" / a.id
    written, skipped = [], []
    for rel, text in files.items():
        dest = root / rel
        if a.write:
            if dest.exists():
                skipped.append(rel)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            written.append(rel)
    if a.json:
        print(json.dumps({"root": str(root), "files": sorted(files), "written": written, "skipped": skipped}, indent=2))
    else:
        for rel in sorted(files):
            tag = "wrote" if rel in written else "exists, kept" if rel in skipped else "would write"
            print(f"{tag}: {root / rel}")
        if not files:
            print("no llm / agent steps: nothing to scaffold")
    return 0


def _cmd_stats(a):
    agg = M.aggregate_metrics(a.runs_root, a.sop)
    if a.json:
        print(json.dumps(agg.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Total runs: {agg.total_runs} (completed: {agg.completed_runs}, failed: {agg.failed_runs}, active: {agg.active_runs})")
        print(f"Success rate: {agg.run_success_rate:.1%}, Avg duration: {agg.avg_run_duration_sec:.1f}s")
        print(f"Tracked steps: {len(agg.step_stats)}")
        for sid, sm in sorted(agg.step_stats.items()):
            print(f"  {sid:<20} invocations={sm.invocations:<4} fail_rate={sm.failure_rate:.1%} avg={sm.avg_duration_sec:.1f}s p50={sm.p50_duration_sec:.1f}s")
    return 0


def _cmd_smells(a):
    repo = _repo(a)
    ir, errs = compile_sop(a.id, repo)
    if ir is None:
        print("; ".join(errs), file=sys.stderr)
        return 1
    agg = M.aggregate_metrics(a.runs_root, a.id)
    findings = SM.detect_all_smells(ir.to_dict(), agg)
    if a.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2, ensure_ascii=False))
    else:
        for f in findings:
            step_part = f"[{f.step_id}] " if f.step_id else ""
            print(f"[{f.severity}] {step_part}{f.smell_id}: {f.message}")
            if f.recommended_refactoring:
                print(f"    -> Suggested refactoring: {f.recommended_refactoring}")
        print(f"{len(findings)} smell finding(s)" if findings else "OK: no smells detected")
    return 0


def _cmd_refactor(a):
    repo = _repo(a)
    path = repo.sop_path(a.id)
    if not path or not path.exists():
        print(f"[error] procedure {a.id} not found", file=sys.stderr)
        return 1
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ir, errs = compile_sop(a.id, repo)
    if ir is None:
        print("; ".join(errs), file=sys.stderr)
        return 1
    agg = M.aggregate_metrics(a.runs_root, a.id)
    findings = SM.detect_all_smells(ir.to_dict(), agg)
    proposals = RF.generate_refactor_proposals(raw, findings)
    if a.json:
        print(json.dumps([p.to_dict() for p in proposals], indent=2, ensure_ascii=False))
    else:
        for p in proposals:
            print(f"[{p.proposal_id}] {p.title} ({p.refactoring_type})")
            print(f"  Rationale: {p.rationale}")
        print(f"{len(proposals)} proposal(s)" if proposals else "OK: no refactoring proposals needed")
    return 0


def _cmd_harvest(a):
    tp = Path(a.traces_file)
    if not tp.exists():
        print(f"[error] traces file not found: {tp}", file=sys.stderr)
        return 1
    trace = HV.parse_trace_from_jsonl(tp) or HV.parse_trace_from_markdown(tp)
    if not trace:
        print(f"[error] failed to parse traces from {tp}", file=sys.stderr)
        return 1
    candidates = HV.cluster_step_patterns([trace], min_frequency=1)
    if not candidates:
        print("no repeated step patterns clustered", file=sys.stderr)
        return 1
    best = candidates[0]
    draft_yaml = HV.synthesize_draft_yaml(a.title, best, sop_id=a.sop_id)
    if a.json:
        print(json.dumps({"candidate": asdict(best) if hasattr(best, "__dataclass_fields__") else best.__dict__, "draft_yaml": draft_yaml}, indent=2))
    else:
        print(f"# Synthesized draft from pattern: {best.pattern_id} ({len(best.step_names)} steps)")
        print(draft_yaml)
    return 0


def _cmd_revise(a):
    revised_p = Path(a.revised_file)
    if not revised_p.exists():
        print(f"[error] revised file not found: {revised_p}", file=sys.stderr)
        return 1
    try:
        revised_def = yaml.safe_load(revised_p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[error] failed to parse revised YAML: {e}", file=sys.stderr)
        return 1

    outcome = RV.apply_revision_with_safety_net(
        sop_file_path=a.file,
        revised_def=revised_def,
        commit_message=a.message,
        create_feature_branch=not a.no_branch,
    )

    if a.json:
        print(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False))
    else:
        status_tag = "[applied]" if outcome.success else f"[{outcome.status.lower()}]"
        print(f"{status_tag} {outcome.sop_id}: {outcome.message}")
        if outcome.commit_sha:
            print(f"commit: {outcome.commit_sha} (branch: {outcome.branch_name})")
        if outcome.judgement and outcome.judgement.errors:
            for err in outcome.judgement.errors:
                print(f"  [error] {err}")
    return 0 if outcome.success else 1


def _cmd_tick(a):
    outcome = LN.run_scheduler_tick(
        schedules_file=a.schedules_file,
        runs_root=a.runs_root,
    )
    if a.json:
        print(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Scheduler tick: {outcome.tick_id} at {outcome.timestamp}")
        print(f"  Schedules checked: {outcome.schedules_checked}, triggered: {len(outcome.schedules_triggered)}")
        print(f"  Suspended runs evaluated: {outcome.resumes_evaluated}, ready: {len(outcome.resumes_ready)}")
        if outcome.errors:
            for err in outcome.errors:
                print(f"  [error] {err}")
    return 0


def _cmd_eval_resume(a):
    root = Path(a.runs_root)
    if a.run_id:
        target_dir = root / a.run_id
        if not target_dir.exists():
            for p in root.glob(f"*/{a.run_id}"):
                target_dir = p
                break
        res = RE.evaluate_run_resumption(target_dir, workspace_root=a.workspace_root)
        results = [res] if res else []
    else:
        results = RE.scan_and_evaluate_all(root, workspace_root=a.workspace_root)

    if a.json:
        print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
    else:
        for r in results:
            tag = "[ready]" if r.can_resume else "[waiting]"
            print(f"{tag} run {r.run_id} ({r.sop_id}): {r.reason}")
        print(f"{len(results)} suspended run(s) evaluated")
    return 0


def _cmd_notify(a):
    events = NT.read_notifications(
        runs_root=a.runs_root,
        kinds=a.kind,
        sop_id=a.sop,
        limit=a.limit,
    )
    if a.json:
        print(json.dumps([e.to_dict() for e in events], indent=2, ensure_ascii=False))
    else:
        for e in events:
            sop_part = f"[{e.sop_id}] " if e.sop_id else ""
            print(f"{e.timestamp} [{e.kind}] {sop_part}{e.message}")
        print(f"{len(events)} notification(s)")
    return 0


def _cmd_java(a):
    cmd = a.java_cmd
    if cmd == "yaml2java":
        p = Path(a.file).resolve()
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        code = J.yaml2java(data, cls_name=a.class_name, package=a.package)
        if a.out:
            out_p = Path(a.out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(code, encoding="utf-8")
            print(f"wrote {a.out}")
        else:
            print(code)
        return 0

    if cmd == "ir2java":
        p = Path(a.file).resolve()
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        code = J.ir2java_interface(data, cls_name=a.class_name, package=a.package)
        if a.out:
            out_p = Path(a.out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(code, encoding="utf-8")
            print(f"wrote {a.out}")
        else:
            print(code)
        return 0

    if cmd == "stub":
        p = Path(a.file).resolve()
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        code = J.stub(data, cls_name=a.class_name, package=a.package)
        if a.out:
            out_p = Path(a.out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(code, encoding="utf-8")
            print(f"wrote {a.out}")
        else:
            print(code)
        return 0

    if cmd == "check-api":
        target = Path(a.path).resolve()
        if target.is_dir():
            violations = J.check_directory(target)
        else:
            violations = J.check_source_content(target.read_text(encoding="utf-8", errors="replace"), file_label=str(target))

        if a.json:
            print(json.dumps([v.to_dict() for v in violations], indent=2, ensure_ascii=False))
        else:
            for v in violations:
                print(f"[violation] {v.file_path}:{v.line_number} {v.rule_description} -> {v.matched_code}")
            print(f"{len(violations)} API violation(s) found" if violations else "OK: no disallowed APIs detected")
        return 1 if violations else 0

    if cmd == "extract":
        target = Path(a.path).resolve()
        if target.is_dir():
            extracted = J.scan_and_extract_directory(target)
        else:
            single = J.extract_from_file(target)
            extracted = [single] if single else []

        if a.json or not a.out:
            print(json.dumps(extracted, indent=2, ensure_ascii=False))
        if a.out:
            out_p = Path(a.out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(extracted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"wrote {a.out}")
        return 0

    return 2


def _cmd_worker(a):
    caps = set(a.caps.split(",")) if a.caps else None
    stats = WK.run_worker_cycle(
        runs_root=a.runs_root,
        agent_name=a.agent,
        agent_caps=caps,
        max_tickets=a.max_tickets,
    )
    if a.json:
        print(json.dumps(stats.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Worker cycle [{a.agent}]:")
        print(f"  Polled: {stats.tickets_polled}, Claimed: {stats.tickets_claimed}, Completed: {stats.tickets_completed}")
        print(f"  Skipped: {stats.tickets_skipped}, Failed: {stats.tickets_failed}")
    return 0


def _cmd_ingest(a):
    db_path = a.db or (Path(a.runs_root) / "metrics.db")
    store = IG.MetricsStore(db_path)
    try:
        if a.query:
            rows = store.query_metrics(sop_id=a.sop, status=a.status)
            if a.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False))
            else:
                for r in rows:
                    print(f"{r['run_id']} [{r['sop_id']}] status={r['status']} dur={r['duration_sec']}s steps={r['total_steps']}")
                print(f"{len(rows)} record(s)")
            return 0

        count = store.scan_and_ingest(a.runs_root)
        if a.json:
            print(json.dumps({"ingested": count, "db": str(db_path)}, indent=2))
        else:
            print(f"Ingested {count} new/updated run record(s) into {db_path}")
        return 0
    finally:
        store.close()


def _cmd_mcp_serve(a):
    MCPS.run_stdio_server()
    return 0


def _cmd_filter_source(a):
    p = Path(a.path).resolve()
    filt = SF.SourceCodeFilter(enabled=True)
    if p.is_dir():
        res = filt.filter_directory(p, block_sensitive_files=not a.dry_run)
        data = res.to_dict()
    else:
        changed, count = filt.filter_file(p, in_place=not a.dry_run)
        data = {"file": str(p), "masked": changed, "redacted_count": count}
    if a.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(f"Filter result for {p}:")
        for k, v in data.items():
            print(f"  {k}: {v}")
    return 0


_HANDLERS = {
    "check": _cmd_check, "compile": _cmd_compile, "graph": _cmd_graph, "config": _cmd_config_show,
    "run": _cmd_run, "next": _cmd_next, "submit": _cmd_submit, "status": _cmd_status,
    "suspend": _cmd_suspend, "resume": _cmd_resume, "cancel": _cmd_cancel, "runs": _cmd_runs,
    "show": _cmd_show, "log": _cmd_log, "steps-json": _cmd_steps_json, "dry-run": _cmd_dry_run,
    "queue": _cmd_queue, "replay": _cmd_replay, "catalog": _cmd_catalog, "scaffold": _cmd_scaffold,
    "stats": _cmd_stats, "smells": _cmd_smells, "refactor": _cmd_refactor, "harvest": _cmd_harvest,
    "revise": _cmd_revise, "tick": _cmd_tick, "eval-resume": _cmd_eval_resume, "notify": _cmd_notify,
    "java": _cmd_java, "worker": _cmd_worker, "ingest": _cmd_ingest, "mcp-serve": _cmd_mcp_serve,
    "filter-source": _cmd_filter_source,
}


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        code = _HANDLERS[a.cmd](a)
    except E.Usage as x:
        print(f"[usage] {x}", file=sys.stderr)
        code = 2
    except cfg.ConfigError as x:
        for m in x.errors:
            print(f"[error] {m}", file=sys.stderr)
        code = x.code
    except L.LockExpired as x:
        print(f"[suspended] {x}", file=sys.stderr)
        code = 4
    except (E.Refused, L.LockBusy, DecisionRefused, R.RecordError) as x:
        print(f"[refused] {_MASK.mask_text(str(x))}", file=sys.stderr)
        code = 1
    except ProcflowError as x:
        print(f"[error] {_MASK.mask_text(str(x))}", file=sys.stderr)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
