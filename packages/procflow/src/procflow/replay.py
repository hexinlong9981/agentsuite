"""Replay: re-check a finished (or running) run against its definition *without calling any provider*.

For a run with ``state.jsonl`` every transition is verified against the IR -- the transition exists with the same
endpoints; for ``by: guard`` the guard is true under the context recorded up to that point; for ``by: decide_by``
the expression's value equals the label; for ``by: label`` the label is one of the candidates -- and the recorded
output of every accepted llm / agent attempt (``ai-attempt-<n>.json``) is validated again against the *current*
checks (output schema, declared outputs, domain rule program, agent ``checks``). Code steps are never re-run.

The IR is the run's own ``ir.json`` by default; with ``current=True`` it is today's compiled definition (and the
implementation root is today's ``_impl/<ID>``). A record that is missing or unreadable is ``undecidable`` --
never silently accepted. :func:`replay_all` returns one verdict per run; the CLI exits 1 unless all are accepted.
"""

from __future__ import annotations

from pathlib import Path

from . import aistep as A
from . import engine as E
from . import guard
from . import records as R
from .compiler import compile_sop

VERDICTS = ("accepted", "rejected", "undecidable")


def _verdict(v, run_id, sop, problems, notes=None, checked=0):
    return {"run_id": run_id, "sop": sop, "verdict": v, "problems": problems, "notes": notes or [], "checked": checked}


class _Shim(E.Engine):
    """Borrows ``Engine.resolver`` / ``_vars`` over a state folded event by event (writes nothing)."""

    def __init__(self, ir):  # noqa: D107 - the parent's __init__ opens a run; this one only needs the state
        self.ir = ir
        self.st = E.State()


def replay_run(rd, repo=None, current=False):
    run = rd.read_run()
    sop = run.get("sop") or rd.sop_id
    if current:
        if repo is None:
            return _verdict("undecidable", rd.run_id, sop, ["--current needs the procedure root"])
        ir, errs = compile_sop(sop, repo)
        if ir is None:
            return _verdict("undecidable", rd.run_id, sop, [f"today's definition does not compile: {errs[:2]}"])
        ir = ir.to_dict()
        impl = Path(repo.root) / "_impl" / sop
    else:
        ir = R.read_json(rd.file("ir.json"))
        if not ir:
            return _verdict("undecidable", rd.run_id, sop, ["ir.json is missing"])
        impl = Path(run["impl_root"]) if run.get("impl_root") else None
    try:
        evs = rd.read_state()
    except R.RecordError as x:
        return _verdict("undecidable", rd.run_id, sop, [str(x)])
    if not evs:
        return _verdict("undecidable", rd.run_id, sop, ["state.jsonl is empty"])
    nodes = {n["id"]: n for n in ir["nodes"]}
    trans = {t["id"]: t for t in ir["transitions"] if t.get("id")}
    ex = ir.get("execution") or {}
    sh = _Shim(ir)
    problems, notes, checked = [], [], 0
    for ev in evs:
        e, at = ev["ev"], f"seq {ev['seq']}"
        tok = sh.st.tokens.get(ev.get("tok"))
        if e == "transition":
            checked += 1
            t = trans.get(ev.get("transition"))
            by = ev.get("by")
            if ev.get("transition") is None:
                targets = [x for x in (ex.get("task_error_target"), ex.get("task_timeout_target")) if x]
                if by in ("error", "loop_limit") and ev["to"] not in targets + [n["on_limit"] for n in nodes.values() if n.get("on_limit")]:
                    problems.append(f"{at}: the {by} transition leads to {ev['to']}, which is not a declared target")
            elif t is None or t["from"] != ev["from"] or t["to"] != ev["to"]:
                problems.append(f"{at}: transition {ev.get('transition')} ({ev['from']} -> {ev['to']}) is not in the IR")
            elif by == "guard" and t.get("guard"):
                if not _eval(sh, t["guard"], tok, problems, at):
                    pass
            elif by == "decide_by":
                node = nodes.get(ev["from"]) or {}
                val = _eval(sh, node.get("decide_by") or "", tok, problems, at, want_value=True)
                if val is not None and str(t.get("label")) != str(val) and not t.get("default"):
                    problems.append(f"{at}: decide_by evaluates to {val!r}, not the label {t.get('label')!r}")
            elif by == "label":
                want = [x.strip() for x in str(ev.get("label") or "").split(",")]
                if not any(w in (str(t.get("label")), t["id"]) or (t.get("default") and w in ("(default)", "default")) for w in want):
                    problems.append(f"{at}: label {ev.get('label')!r} is not a candidate of {t['id']}")
        elif e == "fork" and ev.get("from") in nodes:
            checked += 1
            for c in ev.get("children") or []:
                t = trans.get(c.get("transition"))
                if t is None or t["to"] != c["to"]:
                    problems.append(f"{at}: parallel branch {c.get('transition')} -> {c['to']} is not in the IR")
        elif e == "step_end" and ev.get("step") in nodes and nodes[ev["step"]].get("kind") in ("llm", "agent") \
                and ev.get("result") == "ok" and "decision" not in ev:   # a decision = a person did the step
            checked += 1
            _check_ai(rd, nodes[ev["step"]], ev, impl, sh, problems, notes, at)
        sh.st.apply(ev)
    if not checked:
        return _verdict("undecidable", rd.run_id, sop, ["nothing could be checked"], notes)
    return _verdict("rejected" if problems else "accepted", rd.run_id, sop, problems, notes, checked)


def _eval(sh, text, tok, problems, at, want_value=False):
    try:
        v = guard.evaluate(guard.parse(str(text)), sh.resolver(tok))
    except (guard.GuardError, KeyError) as x:
        problems.append(f"{at}: cannot evaluate {str(text)!r}: {type(x).__name__}")
        return None
    if not want_value and not v:
        problems.append(f"{at}: guard {str(text)!r} is false under the recorded context")
    return v


def _check_ai(rd, node, ev, impl, sh, problems, notes, at):
    f = rd.step_dir(ev["round"], ev["step"], create=False) / f"ai-attempt-{ev['attempt']}.json"
    try:
        rec = R.read_json(f)
    except R.RecordError:
        rec = None
    if not rec or rec.get("output") is None:
        problems.append(f"{at}: the record of {ev['step']} attempt {ev['attempt']} (ai-attempt) is missing")
        return
    try:
        schema, _ = A.schema_for(impl, node)
    except A.AiStepError as x:
        problems.append(f"{at}: {x}")
        return
    ctx = sh._ctx_view(ev.get("tok"))
    errs = A.check_output(rec["output"], schema, node, impl_root=impl, context=ctx,
                          workdir=rd.step_dir(ev["round"], ev["step"], create=False))
    if errs:
        problems.append(f"{at}: the recorded output of {ev['step']} attempt {ev['attempt']} no longer passes the checks: {errs[:2]}")


def replay_all(runs_root, repo=None, sop=None, current=False):
    rows = []
    for p in sorted(Path(runs_root).glob("*/*/run.json")):
        if sop and p.parent.parent.name != sop:
            continue
        rd = R.RunDir(runs_root, p.parent.parent.name, p.parent.name)
        try:
            rows.append(replay_run(rd, repo, current))
        except R.RecordError as x:
            rows.append(_verdict("undecidable", p.parent.name, p.parent.parent.name, [str(x)]))
    return rows


def render(rows):
    cnt = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    lines = [f"replayed {len(rows)} run(s): accepted {cnt['accepted']}, rejected {cnt['rejected']}, undecidable {cnt['undecidable']}"]
    for r in rows:
        lines.append(f"  {r['verdict']:<11} {r['sop']} {r['run_id']} (checked {r['checked']})")
        lines += [f"      - {x}" for x in r["problems"]] + [f"      . {x}" for x in r["notes"]]
    return "\n".join(lines)


def all_accepted(rows):
    return bool(rows) and all(r["verdict"] == "accepted" for r in rows)
