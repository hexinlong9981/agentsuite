"""The execution engine (code / human / choice / loop / parallel / join / finally / rounds).

Not a daemon. Each ``next`` / ``submit`` / ``resume`` advances as far as it can and returns the next
*ticket*. A run executes off its start-time IR (``ir.json``) and config snapshot
(``config.snapshot.json``). ``state.jsonl`` is the record of truth: every change goes through
``Engine.emit`` which appends the event and folds it into :class:`State`; opening a run replays the log.

Tickets (``PROCFLOW_NEXT=<type>``):

  human    wait for a recorded decision; it is only recorded when the approval source confirms it
  work     an llm / agent step whose target is a session: do the work (it is queued for a worker when a registry
           routes it there), then ``submit --output k=v``; llm / agent steps whose target is an inline provider
           (fake / offload) are executed by the engine itself and only produce a ticket when escalated to a person
  command  a code step whose lines the engine will not launch (outside roots / shell syntax): do it, submit
  choice   label branching: ``submit --label <label>``

A ``call`` node starts a child run (children.py): the callee is a concrete ``sop`` or an interface ``dispatch``
decided by code, the child runs under its own records and config narrowed toward the parent's, and its typed
exception (or its outputs) comes back to the call. The engine also does target routing, providers, output validation with
retry-then-escalate, the work queue and budgets (see aistep.py / routing.py / workqueue.py).
"""

from __future__ import annotations

import copy
import json
import secrets
from pathlib import Path

from agent_policy_kit import AuditLog, SecretMasker

from . import agents as AG
from . import aistep as A
from . import config as cfg
from . import children as CH
from . import contract, guard, lock as L, llm as LL, programs as P, records as R
from . import migrate as MG
from . import registry as REG
from . import routing as RT
from . import workqueue as WQ
from .approval import ApprovalSource, DecisionRefused
from .check import Checker
from .compiler import compile_sop
from .children import CallFail
from .defschema import BUILTIN_EXCEPTIONS
from .errors import ProcflowError
from .repo import Repo
from .tracer import NoopTracer, PrefixTracer

ENDED = ("done", "failed", "cancelled")
FAILED_OUTCOMES = {"failure", "failed", "error", "abnormal"}
EXIT_OK, EXIT_REFUSED, EXIT_USAGE, EXIT_SUSPENDED = 0, 1, 2, 4
STEP_BUDGET = 5000
BLOCKED = object()          # a step's return value: the token waits on a child run's ticket (not an endless loop)
EXC_RE = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
TASK_LIKE = ("task",)
_MASK = SecretMasker()


class Refused(ProcflowError):
    """The command is refused (check/config/state/limit). Exit code 1."""


class Usage(ProcflowError):
    """Bad parameters. Exit code 2."""


class Suspend(Exception):
    """Raised inside the engine to stop at a safe boundary."""

    def __init__(self, reason, note):
        super().__init__(note)
        self.reason, self.note = reason, note


# ======================================================================================
# state folded from state.jsonl
# ======================================================================================
class State:
    def __init__(self):
        self.status = "running"
        self.round = 1
        self.inputs, self.ctx, self.outputs = {}, {}, {}
        self.tokens = {}
        self.tok_n = 0
        self.joins = {}
        self.attempts = {}
        self.tickets = {}
        self.done = []
        self.outcomes = []
        self.suspend = None
        self.finals = []
        self.last_seq = 0
        self.ai = {}            # "<round>/<step>" -> {"fb": next fallback index, "tries": rejections, "reasons": [...]}
        self.ai_calls = 0
        self.ai_cost = 0.0
        self.raised = None      # the exception type raised and not (yet) caught
        self.calls = []         # one row per call: step, n, child, sop, dispatch, depth, status, raised ...

    @staticmethod
    def key(rnd, step):
        return f"{rnd}/{step}"

    def new_tok(self):
        return f"t{self.tok_n + 1}"

    def _tok(self, tid, at, frames=None, **kw):
        n = int(tid[1:]) if tid[1:].isdigit() else 0
        self.tok_n = max(self.tok_n, n)
        t = {"at": at, "frames": copy.deepcopy(frames or []), "from": None, "wait": None, "queued": False,
             "done": False, "label": None, "joined": False, "child": None, "call_done": False}
        t.update(kw)
        self.tokens[tid] = t

    def pending(self):
        return [t for t in self.tickets.values() if t.get("pending")]

    def apply(self, ev):  # noqa: C901 - one flat dispatch on the event name
        e = ev["ev"]
        self.last_seq = ev.get("seq", self.last_seq)
        tk = self.tokens.get(ev.get("tok"))
        if e == "start":
            self.inputs, self.ctx, self.round = ev.get("inputs") or {}, ev.get("ctx") or {}, 1
            self._tok(ev["tok"], ev["node"])
        elif e == "enter":
            self.attempts[self.key(ev["round"], ev["step"])] = ev["attempt"]
            if not ev.get("retry"):
                self.ai.pop(self.key(ev["round"], ev["step"]), None)
            if tk:
                tk["done"] = False
        elif e == "ticket":
            self.tickets[ev["ticket"]] = {k: ev.get(k) for k in ("ticket", "type", "tok", "step", "round", "attempt",
                                                                  "ts", "candidates", "path", "queued", "escalated")}
            self.tickets[ev["ticket"]]["pending"] = True
            if tk:
                tk["wait"] = ev["ticket"]
        elif e in ("ticket_done", "ticket_discard"):
            t = self.tickets.get(ev["ticket"])
            if t:
                t["pending"] = False
                t["closed"] = e
                tk2 = self.tokens.get(t["tok"])
                if tk2 and tk2.get("wait") == ev["ticket"]:
                    tk2["wait"] = None
        elif e == "ai_call":
            self.ai_calls += 1
            if ev.get("result") != "unavailable":
                self.ai_cost += float(ev.get("cost") or 0)
            p = self.ai.setdefault(self.key(ev["round"], ev["step"]), {"fb": 0, "tries": 0, "reasons": []})
            fb = int(ev.get("fb") or 0)
            if ev.get("result") == "unavailable":
                p["fb"] = fb + 1
            else:
                p["fb"] = fb
                if ev.get("result") == "invalid":
                    p["tries"] += 1
                    p["reasons"] = list(ev.get("errors") or [])
        elif e == "step_end":
            if ev.get("outputs") is not None and ev.get("result") != "error":
                self.outputs[ev["step"]] = ev["outputs"]
                self.ctx.update(ev.get("ctx_diff") or {})
            self.done.append({k: ev.get(k) for k in ("step", "round", "attempt", "result", "ts", "tok")})
            if tk:
                tk["done"] = True
        elif e == "label":
            if tk:
                tk["label"] = {"label": ev["label"], "by": ev.get("by")}
        elif e == "round":
            self.round = ev["round"]
        elif e == "transition":
            if tk:
                tk.update(at=ev["to"], done=False, label=None, wait=None, joined=False, call_done=False)
                tk["from"], tk["via"] = ev["from"], ev.get("transition")
        elif e == "fork":
            self.tokens.pop(ev["tok"], None)
            for c in ev["children"]:
                self._tok(c["tok"], c["to"], ev.get("frames"), queued=bool(c.get("queued")))
                self.tokens[c["tok"]]["from"] = ev["from"]
                self.tokens[c["tok"]]["via"] = c.get("transition")
        elif e == "activate":
            if tk:
                tk["queued"] = False
        elif e == "join_arrive":
            self.tokens.pop(ev["tok"], None)
            self.joins.setdefault(f"{ev['join']}/{ev['round']}", []).append(ev["source"])
        elif e == "join_fire":
            key = f"{ev['join']}/{ev['round']}"
            if ev.get("mode") == "any":
                self.joins[key] = ["__fired__"]
            else:
                self.joins.pop(key, None)
            self._tok(ev["tok"], ev["join"], ev.get("frames"))
            self.tokens[ev["tok"]]["joined"] = True
        elif e == "join_drop":
            self.tokens.pop(ev["tok"], None)
        elif e == "loop_start":
            if tk:
                tk["frames"].append({"loop": ev["loop"], "mode": ev["mode"], "items": ev.get("items"),
                                     "limit": ev["limit"], "limit_reached": bool(ev.get("limit_reached")),
                                     "var": ev.get("as"), "body": ev["body"], "pos": 0, "item": None, "results": []})
        elif e == "loop_iter":
            if tk:
                fr = tk["frames"][-1]
                fr["item"], fr["pos"] = ev.get("item"), 0
                tk.update(at=ev["to"], done=False)
        elif e == "body_next":
            if tk:
                tk["frames"][-1]["pos"] = ev["pos"]
                tk.update(at=ev["to"], done=False, label=None)
        elif e == "iter_end":
            if tk:
                tk["frames"][-1]["results"].append(ev["result"])
                tk.update(at=ev["loop"], done=False, label=None)
        elif e == "loop_end":
            if tk:
                tk["frames"].pop()
            self.outputs[ev["loop"]] = ev["result"]
        elif e == "token_end":
            self.tokens.pop(ev["tok"], None)
            self.outcomes.append(ev.get("outcome"))
        elif e == "final":
            self.finals.append(ev["step"])
        elif e == "call_start":
            if tk:
                tk["child"] = ev["child"]
            self.calls.append({"step": ev["step"], "n": ev.get("n"), "child": ev["child"], "sop": ev.get("sop"),
                               "interface": ev.get("interface"), "dispatch": ev.get("dispatch"), "depth": ev.get("depth"),
                               "round": self.round, "started": ev.get("ts"), "status": "running", "ended": None,
                               "raised": None})
        elif e == "call_end":
            if tk:
                tk["child"] = None
            for c in self.calls:
                if c["child"] == ev["child"]:
                    c.update(status=ev.get("status"), ended=ev.get("ts"), raised=ev.get("raised"))
            self.ai_calls += int(ev.get("ai_calls") or 0)
            self.ai_cost += float(ev.get("ai_cost") or 0)
            if ev.get("result") == "ok":
                if tk:
                    tk["call_done"] = True
                self.outputs[ev["step"]] = ev.get("outputs") or {}
                self.ctx.update(ev.get("vars") or {})
            self.done.append({k: ev.get(k) for k in ("step", "round", "attempt", "result", "ts", "tok")})
            if tk and ev.get("result") != "ok":
                tk["done"] = False
        elif e == "raise":
            self.raised = ev["type"]
        elif e == "catch":
            self.raised = None
        elif e == "suspend":
            self.status, self.suspend = "suspended", {"reason": ev["reason"], "note": ev.get("note"), "ts": ev.get("ts")}
        elif e == "resume":
            self.status, self.suspend = "running", None
        elif e == "run_end":
            self.status = ev["status"]
            self.tokens.clear()


# ======================================================================================
# the engine
# ======================================================================================
class Engine:
    def __init__(self, rd, *, owner=None, approval=None, tracer=None, impl_root=None, skill_roots=None,
                 audit=None, clock=None, repo=None, providers=None, domain_checks=None):
        self.rd = rd
        self.owner = owner or L.owner_id()
        self.source = approval if isinstance(approval, ApprovalSource) else ApprovalSource(approval)
        self.tracer = tracer or NoopTracer()
        self.clock = clock or R.now
        self.repo = repo
        self.mask = _MASK
        self.audit = audit or AuditLog(str(rd.file("audit.jsonl")))
        run = rd.read_run()
        self.run = run
        if run.get("step_prefix") and not isinstance(self.tracer, PrefixTracer):
            self.tracer = PrefixTracer(self.tracer, run["step_prefix"])      # a child is a part of its root's trace
        self._crepo = None
        self.migrated = []
        self.root_view = None
        root = impl_root or run.get("impl_root")
        self.impl_root = Path(root) if root else None
        self.skill_roots = [Path(x) for x in (skill_roots if skill_roots is not None else run.get("skill_roots") or [])]
        self.ir = R.read_json(rd.file("ir.json"))
        snap = R.read_json(rd.file("config.snapshot.json"))
        self.values = {k: v["value"] for k, v in (snap or {}).get("values", {}).items()}
        self.nodes = {n["id"]: n for n in self.ir["nodes"]}
        self.outs, self.ins = {}, {}
        for t in self.ir["transitions"]:
            self.outs.setdefault(t["from"], []).append(t)
            self.ins.setdefault(t["to"], []).append(t)
        self.vmeta = (snap or {}).get("values", {})
        self.reg = REG.from_run(rd)
        self.providers = providers or {}              # {"llm": {name: LlmService}, "agent": {name: AgentRuntime}} (tests)
        self.domain_checks = domain_checks or {}      # {step id: callable(output, context) -> [reasons]}
        self.queue = WQ.WorkQueue(rd.runs_root, clock=self.clock)
        self.st = State()
        for ev in rd.read_state():
            self.st.apply(ev)
        self.progress = []
        self._locked = False

    # ---- plumbing ------------------------------------------------------------------------
    def cv(self, key, default=None):
        v = self.values.get(key)
        return default if v is None else v

    def now(self):
        return self.clock()

    def emit(self, ev):
        ev = {k: (self.mask.mask_text(v) if isinstance(v, str) and k in ("note", "reason", "message") else v)
              for k, v in ev.items()}
        rec, n, sha = self.rd.append_state(ev, ts=R.iso(self.now()))
        self.st.apply(rec)
        kind = rec["ev"]
        if kind in ("ticket_done", "ticket_discard") and (self.st.tickets.get(rec["ticket"]) or {}).get("queued"):
            self.queue.remove(self.rd.run_id, rec["ticket"])
        if kind in ("enter", "step_end"):
            self.tracer.event("step_start" if kind == "enter" else "step_end", run_id=self.rd.run_id,
                              step=rec.get("step"), round=rec.get("round"), attempt=rec.get("attempt"),
                              result=rec.get("result"), kind=rec.get("kind"), seq=n)
        self._last_sha = sha
        return rec

    def checkpoint(self):
        self.tracer.event("checkpoint", run_id=self.rd.run_id, seq=self.st.last_seq,
                          state_sha256=getattr(self, "_last_sha", None))

    def _lock(self, takeover=False):
        try:
            got = L.acquire(self.rd.file(".lock"), owner=self.owner, takeover=takeover, at=self.now())
        except L.LockExpired as x:
            if self.st.status not in ENDED and self.st.status != "suspended":
                self.suspend("lost-lock", f"lease of {x.holder.get('owner')} expired")
                self.refresh_run()
            raise
        self._locked = True
        if got["took_over"]:
            self.audit.allow(action="lock_takeover", run=self.rd.run_id, previous=got["took_over"].get("owner"),
                             owner=self.owner)
            self.emit({"ev": "takeover", "previous": got["took_over"].get("owner"), "owner": self.owner})

    def _unlock(self):
        if self._locked:
            L.release(self.rd.file(".lock"), owner=self.owner, at=self.now())
            self._locked = False

    # ---- resolving values ------------------------------------------------------------------
    def _vars(self, tok):
        """name -> value visible to placeholders and guards for a token (inputs < context < loop variables)."""
        v = {}
        v.update(self.st.inputs)
        v.update(self.st.ctx)
        for fr in (tok or {}).get("frames", []):
            if fr.get("var"):
                v[fr["var"]] = fr.get("item")
        return v

    def resolver(self, tok):
        vars_ = self._vars(tok)

        loop_vars = {f["var"] for f in (tok or {}).get("frames", []) if f.get("var")}

        def resolve(parts):
            head, rest = parts[0], parts[1:]
            if head in loop_vars:
                return _walk(vars_[head], rest)
            if rest and head in self.st.outputs:
                return _walk(self.st.outputs[head], rest)
            if head in vars_:
                return _walk(vars_[head], rest)
            if head in self.st.outputs and not rest:
                return self.st.outputs[head]
            if head == "round" and not rest:
                return self.st.round
            raise KeyError(head)
        return resolve

    def _eval(self, text, tok):
        try:
            return guard.evaluate(guard.parse(str(text)), self.resolver(tok))
        except guard.GuardError as x:
            raise Suspend("error", f"cannot evaluate expression: {x}") from x

    # ---- the main loop ---------------------------------------------------------------------
    def advance(self):
        st = self.st
        if st.status in ENDED or st.status == "suspended":
            return
        blocked = set()
        try:
            for _ in range(STEP_BUDGET):
                if st.status in ENDED or st.status == "suspended":
                    break
                self._activate_queued()
                tid = next((t for t in sorted(st.tokens, key=lambda x: int(x[1:]))
                            if not st.tokens[t]["wait"] and not st.tokens[t]["queued"] and t not in blocked), None)
                if tid is None:
                    break
                if self._step(tid) is BLOCKED:
                    blocked.add(tid)
            else:
                raise Suspend("limit", f"more than {STEP_BUDGET} engine steps in one advance (endless cycle?)")
            if st.status == "running" and not st.tokens:
                self._finish()
        except Suspend as s:
            self.suspend(s.reason, s.note)
        self.checkpoint()

    def _activate_queued(self):
        limit = int(self.cv("limits.parallel", 4))
        active = sum(1 for t in self.st.tokens.values() if not t["queued"])
        for tid in sorted((t for t, v in self.st.tokens.items() if v["queued"]), key=lambda x: int(x[1:])):
            if active >= limit:
                break
            self.emit({"ev": "activate", "tok": tid})
            active += 1

    def _step(self, tid):
        tok = self.st.tokens[tid]
        node = self.nodes.get(tok["at"])
        if node is None:
            raise Suspend("error", f"token is at unknown node {tok['at']}")
        typ = node["type"]
        if typ == "task":
            if tok["done"]:
                return self._leave(tid, node)
            return self._enter_task(tid, node)
        if typ == "end":
            return self.emit({"ev": "token_end", "tok": tid, "node": node["id"], "outcome": node.get("outcome") or "success"})
        if typ in ("start", "exclusive_choice", "inclusive_choice"):
            return self._leave(tid, node)
        if typ == "parallel_split":
            return self._fork(tid, node, self.outs.get(node["id"], []), {"by": "parallel_split"})
        if typ == "join":
            return self._join(tid, node)
        if typ == "loop":
            return self._loop(tid, node)
        if typ == "call":
            return self._call(tid, node)
        raise Suspend("error", f"{node['id']}: unsupported node type {typ}")

    # ---- tasks ------------------------------------------------------------------------------
    def _attempt(self, step):
        return self.st.attempts.get(State.key(self.st.round, step), 0) + 1

    def _enter_task(self, tid, node):
        sid, kind = node["id"], node.get("kind") or "code"
        if node.get("hook") and not node.get("super_of"):
            # an extension point nobody overrode (an override drops the flag): skip cleanly, whatever its kind
            attempt = self._attempt(sid)
            self.emit({"ev": "enter", "tok": tid, "step": sid, "round": self.st.round, "attempt": attempt, "kind": kind})
            self._step_end(tid, node, attempt, "ok", {}, {"noop": True, "hook": True})
            self.progress.append({"step": sid, "result": "ok", "noop": True, "hook": True})
            return
        if kind not in (self.cv("step_kinds.allowed") or ["code", "llm", "agent", "human"]):
            raise Suspend("error", f"{sid}: step kind {kind} is not allowed by config (step_kinds.allowed)")
        attempt = self._attempt(sid)
        self.emit({"ev": "enter", "tok": tid, "step": sid, "round": self.st.round, "attempt": attempt, "kind": kind})
        if kind == "code":
            return self._run_code(tid, node, attempt)
        if kind == "human":
            return self._issue(tid, node, attempt, "human")
        return self._ai_step(tid, node, attempt)

    def _step_end(self, tid, node, attempt, result, outputs=None, extra=None, keep_all=False):
        outs = outputs if outputs is not None else {}
        declared = list(node.get("outputs") or [])
        kept = dict(outs) if keep_all else ({k: v for k, v in outs.items() if k in declared} if declared else {})
        dropped = sorted(set(outs) - set(kept))
        kept = self.mask.mask_obj(kept)
        ev = {"ev": "step_end", "tok": tid, "step": node["id"], "round": self.st.round, "attempt": attempt,
              "result": result, "outputs": kept, "ctx_diff": {k: v for k, v in kept.items() if k in declared}}
        if dropped:
            ev["dropped_outputs"] = dropped
        ev.update(extra or {})
        self.emit(ev)

    def _run_code(self, tid, node, attempt):
        tok = self.st.tokens[tid]
        lines = P.lines_of(node)
        if not lines:  # nothing to launch: the step is a no-op (hooks, bookkeeping steps)
            self._step_end(tid, node, attempt, "ok", {}, {"noop": True})
            self.progress.append({"step": node["id"], "result": "ok", "noop": True})
            return
        if self._in_quiet_window():
            raise Suspend("limit", f"{node['id']}: inside the quiet window ({self.cv('quiet_window.range')}); no step touching the outside is issued")
        values = self._vars(tok)
        cls = self._classify(node, values)
        if any(c["kind"] != "local" for c in cls):
            return self._issue(tid, node, attempt, "command", lines=[{"line": c["line"], "why": c.get("why")} for c in cls])
        retries = int(self.cv("retry.code", 1))
        res = None
        for i in range(retries + 1):
            if i:
                attempt = self._attempt(node["id"])
                self.emit({"ev": "enter", "tok": tid, "step": node["id"], "round": self.st.round, "attempt": attempt,
                           "kind": "code", "retry": i})
            res = self._exec(node, attempt, cls, values)
            if res["ok"] and "__raise__" in (res["outputs"] or {}):
                # the program asked to raise a typed exception: not retried, goes to the error route
                req = res["outputs"].pop("__raise__")
                exc, note = (req.get("type"), req.get("note")) if isinstance(req, dict) else (req, None)
                self._step_end(tid, node, attempt, "error", None, {"evidence": res["evidence"], "why": "raised"})
                self._record_raise(tid, node, str(exc), "step", note)
                self.progress.append({"step": node["id"], "result": "error", "why": f"raised {exc}", **res["summary"]})
                return self._after_error(tid, node)
            if res["ok"]:
                self._step_end(tid, node, attempt, "ok", res["outputs"], {"evidence": res["evidence"]})
                self.progress.append({"step": node["id"], "result": "ok", **res["summary"]})
                return
        self._step_end(tid, node, attempt, "error", None, {"evidence": res["evidence"], "why": res["why"]})
        self.progress.append({"step": node["id"], "result": "error", "why": res["why"], **res["summary"]})
        self._after_error(tid, node, timeout=res.get("timeout", False))

    def _classify(self, node, values):
        ed = self.rd.step_dir(self.st.round, node["id"], create=False)
        vals = dict(values)
        vals.update({"evidence": str(ed), "run": str(ed), "run_dir": str(self.rd.path), "input": str(ed / "inputs.json")})
        roots = P.allowed_roots(self.impl_root, self.skill_roots)
        out = []
        for ln in P.lines_of(node):
            out.append(P.classify(ln, vals, roots, self.impl_root))
        for c in out:
            if c["kind"] == "local":
                self.audit.allow(action="program", run=self.rd.run_id, step=node["id"],
                                 argv_sha256=R.sha256_json(c["argv"]))
            else:
                self.audit.deny(c.get("why", ""), action="program", run=self.rd.run_id, step=node["id"])
        return out

    def _exec(self, node, attempt, cls, values):
        ed = self.rd.step_dir(self.st.round, node["id"])
        inputs_path = ed / "inputs.json"
        outputs_path = ed / f"outputs-{attempt}.json"
        R.write_json(inputs_path, self.mask.mask_obj({"inputs": self.st.inputs, "context": self.st.ctx,
                                                      "vars": values, "round": self.st.round}))
        outputs_path.unlink(missing_ok=True)
        timeout_s = cfg.duration_seconds(self.cv("timeout.step", "2h")) or 7200
        evidence, codes, secs, outb, errb = [], [], 0.0, 0, 0
        ok, why, timed_out = True, None, False
        for c in cls:
            evidence.append(R.write_evidence(ed, "command.txt", " ".join(c["argv"]) + "\n", self.mask))
            r = P.run_local(c["argv"], ed, timeout_s, inputs_path, outputs_path)
            so = R.write_evidence(ed, "stdout.txt", r["stdout"], self.mask)
            se = R.write_evidence(ed, "stderr.txt", r["stderr"], self.mask)
            evidence += [so, se]
            outb, errb, secs = outb + so["bytes"], errb + se["bytes"], secs + r["seconds"]
            codes.append(r["exit_code"])
            if r["timeout"]:
                ok, why, timed_out = False, f"timeout after {timeout_s}s", True
                break
            if r["exit_code"] != 0:
                ok, why = False, f"exit code {r['exit_code']}"
                break
        outputs = {}
        if ok:
            try:
                outputs = P.read_outputs(outputs_path)
            except (ValueError, OSError) as x:
                ok, why = False, f"unreadable outputs file: {x}"
        summary = {"lines": len(cls), "exit_codes": codes, "seconds": round(secs, 3), "stdout_bytes": outb,
                   "stderr_bytes": errb, "evidence_dir": str(Path(str(self.st.round)) / ed.name)}
        R.write_json(ed / f"attempt-{attempt}.json", {"attempt": attempt, "ok": ok, "why": why, **summary})
        return {"ok": ok, "why": why, "timeout": timed_out, "outputs": outputs, "evidence": evidence, "summary": summary}

    def _in_quiet_window(self):
        rng = self.cv("quiet_window.range")
        return bool(rng) and _in_window(rng, self.now())

    def _after_error(self, tid, node, timeout=False):
        tok = self.st.tokens[tid]
        fr = tok["frames"][-1] if tok["frames"] else None
        if fr and node["id"] == fr["body"][fr["pos"]]:
            return self._body_next(tid, fr, failed=True)
        ex = self.ir.get("execution") or {}
        target = (ex.get("task_timeout_target") if timeout else None) or ex.get("task_error_target")
        if target and target in self.nodes:
            return self._take(tid, node, [{"id": None, "from": node["id"], "to": target}], {"by": "error"})
        raise Suspend("error", f"{node['id']} failed and no task_error_target is defined")

    # ---- exceptions -----------------------------------------------------------------
    def _record_raise(self, tid, node, exc, source, note=None, child=None):
        """Record a raise. A step may only raise a type it declares (or a built-in); anything else becomes
        ``SystemError`` and the requested type is kept. Returns the recorded type."""
        exc = str(exc)
        if source == "step":
            declared = bool(EXC_RE.match(exc)) and (exc in BUILTIN_EXCEPTIONS or exc in (node.get("raises") or []))
        else:
            declared = True
        typ = exc if declared else "SystemError"
        ev = {"ev": "raise", "tok": tid, "step": node["id"], "type": typ, "source": source, "declared": declared,
              "note": (note or "")[:300] or None}
        if not declared:
            ev["requested"] = self.mask.mask_text(exc)[:64]
        if child:
            ev["child"] = child
        self.emit(ev)
        return typ

    def _route_exception(self, tid, node, exc):
        """A call's ``catch`` takes the exception; otherwise ``task_error_target``; otherwise suspend (limit)."""
        tgt = (node.get("catch") or {}).get(exc)
        if tgt and tgt in self.nodes:
            self.emit({"ev": "catch", "tok": tid, "step": node["id"], "type": exc, "to": tgt})
            self.emit({"ev": "transition", "tok": tid, "from": node["id"], "to": tgt, "transition": None, "by": "catch",
                       "exception": exc})
            return
        target = (self.ir.get("execution") or {}).get("task_error_target")
        if not target or target not in self.nodes:
            raise Suspend("limit", f"{node['id']}: exception {exc} has no catch and there is no task_error_target")
        self.emit({"ev": "transition", "tok": tid, "from": node["id"], "to": target, "transition": None, "by": "error",
                   "exception": exc})

    def _raise_at(self, tid, node, exc, note, source="engine", child=None):
        typ = self._record_raise(tid, node, exc, source, note, child=child)
        self.progress.append({"step": node["id"], "result": "error", "why": f"{typ}: {note}"[:200]})
        self._route_exception(tid, node, typ)

    # ---- call -----------------------------------------------------------------------
    def call_repo(self):
        """The procedure root used to compile a callee / a newer version (the run's own record when none was given)."""
        if self.repo is not None:
            return self.repo
        if self._crepo is None:
            c = self.run.get("repo") or {}
            if c.get("root") and Path(c["root"]).is_dir():
                self._crepo = Repo(c["root"], c.get("interfaces_dir"), c.get("config_dir"))
        return self._crepo

    def rebind_ir(self, ir):
        """Use ``ir`` from now on (after ``resume --migrate``)."""
        self.ir = ir
        self.nodes = {n["id"]: n for n in ir["nodes"]}
        self.outs, self.ins = {}, {}
        for t in ir["transitions"]:
            self.outs.setdefault(t["from"], []).append(t)
            self.ins.setdefault(t["to"], []).append(t)

    def active_children(self):
        return [t["child"] for t in self.st.tokens.values() if t.get("child")]

    def root_id(self):
        return R.root_run_id(self.rd.runs_root, self.rd.run_id, self.run)

    def _child_engine(self, child_id):
        crd = R.find_run(self.rd.runs_root, child_id)
        return Engine(crd, owner=self.owner, approval=self.source, tracer=self.tracer, repo=self.call_repo(),
                      providers=self.providers, clock=self.clock)

    def _eval_call(self, text, tok, what):
        try:
            return guard.evaluate(guard.parse(str(text)), self.resolver(tok))
        except guard.GuardError as x:
            raise CallFail("SystemError", f"{what}: cannot evaluate {str(text)[:80]!r}: {x}") from x

    def _call_target(self, node, tok):
        """The callee and how it was chosen. ``dispatch`` is a coded predicate, first true wins (never an LLM)."""
        if node.get("sop"):
            return node["sop"], {"by": "sop", "reason": node.get("reason")}
        evals = []
        for i, d in enumerate(node.get("dispatch") or []):
            v = bool(self._eval_call(d["when"], tok, f"{node['id']}.dispatch[{i}]"))
            evals.append({"index": i, "when": d["when"], "sop": d["sop"], "value": v})
            if v:
                return d["sop"], {"by": "dispatch", "index": i, "when": d["when"], "evals": evals}
        di = (node.get("interface_spec") or {}).get("default_impl")
        if di:
            return di, {"by": "default_impl", "evals": evals}
        return None, {"by": "none", "evals": evals}

    def _call(self, tid, node):
        tok = self.st.tokens[tid]
        if tok.get("child"):
            return self._drive_child(tid, node)
        if tok.get("call_done"):
            return self._leave(tid, node)
        return self._enter_call(tid, node)

    def _enter_call(self, tid, node):
        sid, tok = node["id"], self.st.tokens[tid]
        attempt = self._attempt(sid)
        self.emit({"ev": "enter", "tok": tid, "step": sid, "round": self.st.round, "attempt": attempt, "kind": "call"})
        try:
            target, how = self._call_target(node, tok)
            if target is None:
                return self._raise_at(tid, node, "NoImplementation",
                                      f"no dispatch predicate is true and {node.get('interface')} has no default_impl")
            depth = int(self.run.get("depth") or 0) + 1
            lims = [x for x in (self.cv("limits.max_depth"), node.get("max_depth"), self.run.get("depth_limit"))
                    if isinstance(x, int) and not isinstance(x, bool)]
            limit = min(lims) if lims else 0
            if depth > limit:
                return self._raise_at(tid, node, "LimitReached",
                                      f"call depth {depth} exceeds the limit {limit} (limits.max_depth, max_depth, the parent's remaining)")
            values = {}
            for k, v in (node.get("with") or {}).items():
                values[k] = self._eval_call(v, tok, f"{sid}.with.{k}") if isinstance(v, str) else copy.deepcopy(v)
            spec = node.get("interface_spec")
            if spec:
                full = contract.with_defaults(spec.get("inputs"), values)
                bad = contract.check_types(spec.get("inputs"), full) + contract.check(spec.get("pre"), full)
                if bad:
                    raise CallFail("SystemError", f"{node.get('interface')}: inputs / precondition not met: {bad}")
            n = 1 + len([c for c in self.st.calls if c["step"] == sid])
            crd, notes = CH.create_child_run(self, node, target, values, n, how, limit, depth)
        except CallFail as x:
            return self._raise_at(tid, node, x.exc, x.note)
        self.audit.allow(action="call", run=self.rd.run_id, step=sid, child=crd.run_id, sop=target, by=how.get("by"))
        self.emit({"ev": "call_start", "tok": tid, "step": sid, "child": crd.run_id, "sop": target, "n": n,
                   "interface": node.get("interface"), "depth": depth, "depth_limit": limit, "dispatch": how,
                   "inputs_sha256": R.sha256_json(values),
                   "narrowed": [x["key"] for x in notes if not x.get("ignored")]})
        self.progress.append({"step": sid, "result": "call", "child": crd.run_id, "sop": target})
        return self._drive_child(tid, node)

    def _drive_child(self, tid, node):
        cid = self.st.tokens[tid]["child"]
        try:
            ceng = self._child_engine(cid)
        except R.RecordError as x:
            raise Suspend("error", f"child run {cid} cannot be opened: {x}") from x
        if ceng.st.status == "suspended":
            s = ceng.st.suspend or {}
            raise Suspend("child", f"child run {cid} is suspended ({s.get('reason')}: {s.get('note')})")
        if ceng.st.status not in ENDED:
            ceng._lock()
            try:
                ceng._check_human_timeout()
                ceng.advance()
            finally:
                ceng.refresh_run()
                ceng._unlock()
            self.progress += [dict(p, step=f"{node['id']}/{p['step']}") for p in ceng.progress if "step" in p]
            if ceng.st.status == "suspended":
                s = ceng.st.suspend or {}
                raise Suspend("child", f"child run {cid} is suspended ({s.get('reason')}: {s.get('note')})")
        if ceng.st.status not in ENDED:
            return BLOCKED
        return self._end_call(tid, node, ceng)

    def _end_call(self, tid, node, ceng):
        sid, status = node["id"], ceng.st.status
        spec = node.get("interface_spec")
        citf = ceng.ir.get("interface") or {}
        declared = spec is not None or citf.get("origin") == "declared"
        out_spec = ((spec or citf).get("outputs")) or {}
        exc, why, outs = None, None, {}
        if status == "done":
            outs = ceng._final_outputs()
            bad = contract.check_types(out_spec, outs) if declared else []
            if spec:
                bad += contract.check(spec.get("post"), {**contract.with_defaults(spec.get("inputs"), ceng.st.inputs), **outs})
            if bad:
                exc, why = "SystemError", f"the child's outputs do not meet the contract: {bad}"
        else:
            exc = (ceng.run.get("raised") or ceng.st.raised) if status == "failed" else None
            exc = exc or "SystemError"
            allowed = set((spec or citf).get("raises") or []) | set(BUILTIN_EXCEPTIONS)
            if exc not in allowed:
                why = f"the child raised {exc}, which {node.get('interface') or node.get('sop')} does not declare (received as SystemError)"
                exc = "SystemError"
            else:
                why = f"the child run {status} (exception {exc})"
        usage = {"ai_calls": ceng.st.ai_calls, "ai_cost": ceng.st.ai_cost}
        base = {"ev": "call_end", "tok": tid, "step": sid, "child": ceng.rd.run_id, "status": status,
                "round": self.st.round, "attempt": self.st.attempts.get(State.key(self.st.round, sid), 1), **usage}
        if exc is None:
            vars_ = {str(dest): outs.get(k) for k, dest in (node.get("returns") or {}).items()}
            self.emit({**base, "result": "ok", "outputs": self.mask.mask_obj({"result": "ok", "child_run": ceng.rd.run_id, **outs}),
                       "vars": self.mask.mask_obj(vars_), "outputs_sha256": R.sha256_json(outs)})
            self.progress.append({"step": sid, "result": "ok", "child": ceng.rd.run_id})
            return None
        self.emit({**base, "result": "error", "raised": exc})
        self._raise_at(tid, node, exc, why, source="child", child=ceng.rd.run_id)
        return None

    def _first_child_ticket(self, d):
        """The first pending ticket below a described run: ``(run_id, ticket)`` or None."""
        for k in d.get("children") or []:
            if k.get("ticket"):
                return k["run_id"], k["ticket"]
            hit = self._first_child_ticket(k)
            if hit:
                return hit
        return None

    def _propagate_up(self):
        """After a child advanced on its own (submit / next on the child's id): advance its parents up to the root."""
        par = self.run.get("parent")
        if not par:
            return
        try:
            peng = Engine(R.find_run(self.rd.runs_root, par["run_id"]), owner=self.owner, approval=self.source,
                          tracer=self.tracer, repo=self.call_repo(), providers=self.providers, clock=self.clock)
        except R.RecordError:
            return
        if peng.st.status in ENDED or peng.st.status == "suspended":
            self.root_view = {"run_id": peng.rd.run_id, "status": peng.display_status()}
            return
        peng._lock()
        try:
            peng.advance()
        finally:
            peng.refresh_run()
            peng._unlock()
        peng._propagate_up()
        self.root_view = peng.root_view or {"run_id": peng.rd.run_id, "status": peng.display_status()}

    def _suspend_children(self, note):
        """A suspended parent suspends its running children (deepest first, via their own ``suspend``)."""
        for cid in self.active_children():
            try:
                ceng = self._child_engine(cid)
                if ceng.st.status in ENDED or ceng.st.status == "suspended":
                    continue
                ceng._lock()
                try:
                    ceng.suspend("parent", note)
                finally:
                    ceng.refresh_run()
                    ceng._unlock()
            except ProcflowError as x:
                self.audit.deny(str(x)[:200], action="suspend_child", run=self.rd.run_id, child=cid)

    def _cancel_children(self, reason):
        for tid, tok in list(self.st.tokens.items()):
            cid = tok.get("child")
            if not cid:
                continue
            ceng = self._child_engine(cid)
            if ceng.st.status not in ENDED:
                ceng._lock()
                try:
                    ceng._cancel_children(reason)
                    ceng.emit({"ev": "cancel", "reason": f"parent cancelled: {reason}"})
                    ceng._finish(cancelled=True)
                finally:
                    ceng.refresh_run()
                    ceng._unlock()
            self.emit({"ev": "call_end", "tok": tid, "step": tok["at"], "child": cid, "status": "cancelled",
                       "result": "cancelled", "round": self.st.round, "ai_calls": ceng.st.ai_calls,
                       "ai_cost": ceng.st.ai_cost})

    # ---- tickets ----------------------------------------------------------------------------
    def _issue(self, tid, node, attempt, typ, lines=None, candidates=None, extra=None):
        sid = node["id"]
        ed = self.rd.step_dir(self.st.round, sid)
        ticket = f"{sid}.{self.st.round}.{attempt}" + (".choice" if typ == "choice" else "")
        doc = {"ticket": ticket, "type": typ, "step": sid, "round": self.st.round, "attempt": attempt,
               "kind": node.get("kind"), "title": self.mask.mask_text(str(node.get("title") or "")),
               "outputs": list(node.get("outputs") or []), "evidence_expected": list(node.get("evidence") or []),
               "evidence_dir": f"{self.st.round}/{ed.name}"}
        if typ in ("work", "command") or (extra or {}).get("escalated"):
            ctx_path = ed / f"context-{attempt}.json"
            R.write_json(ctx_path, self.mask.mask_obj({"inputs": self.st.inputs, "context": self.st.ctx,
                                                       "vars": self._vars(self.st.tokens[tid])}))
            doc["context_file"] = ctx_path.name
            doc["action"] = self.mask.mask_text(str((node.get("def") or {}).get("action") or ""))
        if lines:
            doc["lines"] = lines
        if candidates:
            doc["candidates"] = candidates
        if extra:
            doc.update(self.mask.mask_obj(extra))
        path = ed / (f"ticket-{attempt}-choice.json" if typ == "choice" else f"ticket-{attempt}.json")
        R.write_json(path, doc)
        rel = f"{self.st.round}/{ed.name}/{path.name}"
        self.emit({"ev": "ticket", "tok": tid, "ticket": ticket, "type": typ, "step": sid, "round": self.st.round,
                   "attempt": attempt, "candidates": candidates, "path": rel, "queued": bool((extra or {}).get("queued")),
                   "escalated": bool((extra or {}).get("escalated"))})
        return doc

    # ---- llm / agent steps --------------------------------------------------------------
    def _retry_limit(self, node):
        cap = int(self.cv("retry.llm", 2))
        r = (node.get("def") or {}).get("retry")
        if isinstance(r, dict) and isinstance(r.get("max"), int) and not isinstance(r.get("max"), bool):
            return max(0, min(cap, r["max"]))
        return cap

    def _bump_attempt(self, tid, node):
        attempt = self._attempt(node["id"])
        self.emit({"ev": "enter", "tok": tid, "step": node["id"], "round": self.st.round, "attempt": attempt,
                   "kind": node.get("kind"), "retry": True})
        return attempt

    def _ai_budget(self, node, kind, name):
        mx = int(self.cv("budget.max_calls_per_run", 100))
        if self.st.ai_calls >= mx:
            raise Suspend("limit", f"{node['id']}: budget.max_calls_per_run={mx} is used up")
        ceil = self.cv("budget.max_cost_per_run")
        if ceil is not None:
            est = float((self.reg.entry(kind, name) or {}).get("cost_per_call") or 0)
            if self.st.ai_cost + est > float(ceil):
                raise Suspend("limit", f"{node['id']}: the next call would exceed budget.max_cost_per_run={ceil} "
                                       f"(spent {round(self.st.ai_cost, 6)}, estimate {est})")

    def _provider(self, kind, name):
        inj = (self.providers.get(kind) or {}).get(name)
        if inj is not None:
            return inj
        ent = self.reg.entry(kind, name) or {}
        if ent.get("provider") == "fake" and not REG.fake_allowed():
            ent = dict(ent, provider="disabled", disabled_reason="fake providers are not allowed in this environment")
        return LL.service(name, ent) if kind == "llm" else AG.runtime(name, ent)

    def _ctx_view(self, tid=None):
        v = dict(self._vars(self.st.tokens.get(tid) if tid else None))
        v.update(self.st.outputs)
        return v

    def _ai_doc(self, tid, node, attempt, reasons):
        d = node.get("def") or {}
        return {"ticket": f"{node['id']}.{self.st.round}.{attempt}", "sop": self.run.get("sop"), "step": node["id"],
                "attempt": attempt, "action": self.mask.mask_text(str(d.get("action") or "")),
                "completion": self.mask.mask_text(str(d.get("completion") or "")),
                "inputs": self.mask.mask_obj(self._vars(self.st.tokens.get(tid))),
                "expected_outputs": list(node.get("outputs") or []), "rejections": list(reasons or [])}

    def _ai_record(self, tid, node, attempt, route, name, model, fb, result, *, prompt=None, output=None, errors=None,
                   cost=None, via=None, files=None):
        """Write ``ai-attempt-<n>.json`` (masked) and the ``ai_call`` event."""
        ed = self.rd.step_dir(self.st.round, node["id"])
        key = State.key(self.st.round, node["id"])
        prog = self.st.ai.get(key) or {"tries": 0}
        rec = {"attempt": attempt, "result": result,
               "route": {k: route.get(k) for k in ("kind", "granularity", "layer", "source", "data_class", "needs")},
               "target": name, "fallback_index": fb, "tries": prog.get("tries", 0) + (1 if result == "invalid" else 0),
               "model": model, "via": via, "params": route.get("params") or {}, "cost": cost,
               "prompt": prompt, "output": output, "check_errors": list(errors or []), "files": files or []}
        R.write_json(ed / f"ai-attempt-{attempt}.json", self.mask.mask_obj(rec))
        self.emit({"ev": "ai_call", "tok": tid, "step": node["id"], "round": self.st.round, "attempt": attempt,
                   "kind": route.get("kind"), "target": name, "model": model, "via": via, "result": result, "fb": fb,
                   "cost": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0,
                   "errors": self.mask.mask_obj([str(x)[:300] for x in (errors or [])][:5])})

    def _ai_escalate(self, tid, node, attempt, reason, rejections=None, bump=False):
        """Hand the step to a person (the human path); the person supplies the outputs."""
        if bump:
            attempt = self._bump_attempt(tid, node)
        self.emit({"ev": "escalate", "tok": tid, "step": node["id"], "round": self.st.round, "attempt": attempt,
                   "to": "human", "reason": reason})
        self.audit.allow(action="ai_escalate", run=self.rd.run_id, step=node["id"], reason=str(reason)[:120])
        self._issue(tid, node, attempt, "human", extra={"escalated": True, "escalation_reason": str(reason)[:300],
                                                        "rejections": list(rejections or [])})
        self.progress.append({"step": node["id"], "result": "escalated", "why": str(reason)[:120]})

    def _ai_step(self, tid, node, attempt):  # noqa: C901 - the whole retry / fallback / escalate flow in one place
        sid, key = node["id"], State.key(self.st.round, node["id"])
        route = RT.resolve(node, self.vmeta, self.reg)
        if route["target"] is None:
            if route.get("to_human"):
                return self._ai_escalate(tid, node, attempt, route["to_human"])
            why = "; ".join(c["why"] for c in route["candidates"] if c["why"]) or "no candidate"
            raise Suspend("limit", f"{sid}: no execution target passes the routing filters ({why})")
        kind = route["kind"]
        chain = [(route["target"], route["model"])] + [(f["target"], f["model"]) for f in route["fallback"]]
        try:
            schema, _ = A.schema_for(self.impl_root, node)
            template, _ = A.template_for(self.impl_root, node)
        except A.AiStepError as x:
            raise Suspend("error", str(x)) from x
        ed = self.rd.step_dir(self.st.round, sid)
        first = True
        while True:
            prog = self.st.ai.get(key) or {"fb": 0, "tries": 0, "reasons": []}
            fb = prog["fb"]
            if fb >= len(chain):
                raise Suspend("limit", f"{sid}: every routed target failed or was unavailable")
            if not first:
                attempt = self._bump_attempt(tid, node)
            first = False
            name, model = chain[fb]
            self._ai_budget(node, kind, name)
            prov = self._provider(kind, name)
            why = prov.available()
            if why:
                self._ai_record(tid, node, attempt, route, name, model, fb, "unavailable", errors=[why], via=None)
                continue
            doc = self._ai_doc(tid, node, attempt, prog.get("reasons"))
            prompt = self.mask.mask_text(A.build_prompt(doc, schema, template, agent=(kind == "agent")))
            if prov.mode == "ticket":
                return self._ai_queue(tid, node, attempt, route, name, model, fb, prompt, doc)
            res = self._ai_invoke(kind, prov, node, attempt, prompt, model, route, schema, ed)
            if res["status"] == "refused":
                self._ai_record(tid, node, attempt, route, name, model, fb, "refused", prompt=prompt,
                                errors=[res["message"]], via=res.get("via"))
                return self._ai_escalate(tid, node, attempt, f"{name} refused the content; it is not sent elsewhere",
                                         [res["message"]], bump=True)
            if res["status"] == "unavailable":
                self._ai_record(tid, node, attempt, route, name, model, fb, "unavailable", prompt=prompt,
                                errors=[res["message"]], via=res.get("via"))
                continue
            errors = res["errors"]
            self._ai_record(tid, node, attempt, route, name, model, fb, "invalid" if errors else "ok", prompt=prompt,
                            output=res.get("output"), errors=errors, cost=res.get("cost"), via=res.get("via"),
                            files=res.get("files"))
            if errors:
                if self.st.ai[key]["tries"] > self._retry_limit(node):
                    return self._ai_escalate(tid, node, attempt, f"the output was rejected {self.st.ai[key]['tries']} time(s)",
                                             errors, bump=True)
                continue
            outs = dict(res["output"])
            self._step_end(tid, node, attempt, "ok", outs, {"ai": {"target": name, "via": res.get("via"),
                                                                    "attempt_file": f"ai-attempt-{attempt}.json"}})
            self.progress.append({"step": sid, "result": "ok", "target": name, "via": res.get("via")})
            return None

    def _ai_invoke(self, kind, prov, node, attempt, prompt, model, route, schema, ed):
        """One inline provider call -> ``{"status": ok|unavailable|refused, "errors", "output", "cost", ...}``."""
        sid = node["id"]
        timeout = cfg.duration_seconds(self.cv("timeout.step", "2h")) or 7200
        ctx = self._ctx_view()
        try:
            if kind == "llm":
                r = prov.call(LL.LlmRequest(prompt=prompt, model=model, params=route.get("params") or {}, schema=schema,
                                            step=sid, attempt=attempt, data_class=route.get("data_class"), timeout=timeout))
            else:
                wd = ed / f"agent-{attempt}"
                (wd / "input").mkdir(parents=True, exist_ok=True)
                (wd / "output").mkdir(parents=True, exist_ok=True)
                R.write_json(wd / "input" / "context.json", self.mask.mask_obj(ctx))
                r = prov.run(AG.AgentRequest(prompt=prompt, workdir=wd, inputs=[], model=model, params=route.get("params") or {},
                                             needs=route.get("needs") or [], step=sid, attempt=attempt, timeout=timeout))
        except Exception as x:  # noqa: BLE001 - a provider bug is "unavailable", never a crash or an accept
            return {"status": "unavailable", "message": f"provider raised {type(x).__name__}", "via": None}
        via = r.via
        if not r.ok:
            kind_ = r.error_kind or "unavailable"
            if kind_ == "refused":
                return {"status": "refused", "message": str(r.error)[:300], "via": via}
            if kind_ != "invalid_output":
                return {"status": "unavailable", "message": f"{kind_}: {str(r.error)[:300]}", "via": via}
            return {"status": "ok", "errors": [str(r.error)[:300]], "output": None, "cost": r.cost, "via": via}
        files = None
        if kind == "llm":
            value, perr = A.parse_output(r.text, node)
            wd = ed
        else:
            info = AG.collect(wd)
            files = info["files"]
            problems = list(info["problems"]) + [f"wrote outside the output directory: {x}" for x in r.outside_writes]
            value = info["summary"]
            perr = "; ".join(problems) if problems else (None if value is not None else "output/result.json is missing")
            if problems and value is not None:
                return {"status": "ok", "errors": problems, "output": value, "cost": r.cost, "via": via, "files": files}
        if perr:
            return {"status": "ok", "errors": [perr], "output": None, "cost": r.cost, "via": via, "files": files}
        errors = A.check_output(value, schema, node, impl_root=self.impl_root, context=ctx, workdir=ed,
                                extra_rule=self.domain_checks.get(sid))
        return {"status": "ok", "errors": errors, "output": value, "cost": r.cost, "via": via, "files": files}

    def _ai_queue(self, tid, node, attempt, route, name, model, fb, prompt, doc):
        """A session target: write the prompt, issue a work ticket and (registry configured) put it on the queue."""
        sid = node["id"]
        ed = self.rd.step_dir(self.st.round, sid)
        pf = ed / f"prompt-{attempt}.md"
        R.write_atomic(pf, prompt)
        queued = self.reg.configured or route["granularity"] != "builtin"
        extra = {"ai": True, "target": name, "model": model, "via": "session", "prompt_file": pf.name,
                 "fallback_index": fb, "rejections": doc["rejections"], "data_class": route["data_class"],
                 "ai_kind": route["kind"], "queued": queued}
        tdoc = self._issue(tid, node, attempt, "work", extra=extra)
        if queued:
            self.queue.enqueue({"run_id": self.rd.run_id, "sop": self.run["sop"], "ticket": tdoc["ticket"], "step": sid,
                                "round": self.st.round, "attempt": attempt, "kind": route["kind"], "target": name,
                                "path": f"{self.st.round}/{ed.name}/ticket-{attempt}.json",
                                "run": f"{self.rd.sop_id}/{self.rd.run_id}"})
        # the run now waits for a worker; no command holds the lease (every command releases it on exit), so any
        # worker session can claim the ticket and submit
        return tdoc

    def _require_claimant(self, t, by):
        c = self.queue.claimant(self.rd.run_id, t["ticket"])
        who = WQ.safe_name(by or self.owner, default="worker")
        if c is None:
            raise Refused(f"{t['ticket']}: a queued ticket must be claimed first (`queue --claim --ticket {t['ticket']}`); "
                          "only the claimant may submit")
        if c["expired"]:
            raise Refused(f"{t['ticket']}: the claim by {c['worker']} expired; claim the ticket again")
        if c["worker"] != who:
            raise Refused(f"{t['ticket']}: claimed by another worker; only the claimant may submit")

    def _ai_submitted(self, tid, node, t, outputs, by):
        """Validate a worker-submitted output; record it. Returns the rejection reasons (empty = accepted)."""
        tdoc = R.read_json(self.rd.path / t["path"], {}) or {}
        sid, attempt = node["id"], t["attempt"]
        try:
            schema, _ = A.schema_for(self.impl_root, node)
        except A.AiStepError as x:
            raise Suspend("error", str(x)) from x
        route = RT.resolve(node, self.vmeta, self.reg)
        errors = A.check_output(outputs, schema, node, impl_root=self.impl_root, context=self._ctx_view(tid),
                                workdir=self.rd.step_dir(self.st.round, sid), extra_rule=self.domain_checks.get(sid))
        ed = self.rd.step_dir(t["round"], sid)
        prompt = None
        if tdoc.get("prompt_file") and (ed / tdoc["prompt_file"]).is_file():
            prompt = (ed / tdoc["prompt_file"]).read_text(encoding="utf-8")
        cost = float((self.reg.entry(route["kind"], tdoc.get("target")) or {}).get("cost_per_call") or 0)
        self._ai_record(tid, node, attempt, route, tdoc.get("target") or route.get("target"), tdoc.get("model"),
                        int(tdoc.get("fallback_index") or 0), "invalid" if errors else "ok", prompt=prompt,
                        output=outputs, errors=errors, cost=cost, via="session" if t.get("queued") else "manual")
        return errors

    def _ai_rejected(self, tid, node, errors):
        key = State.key(self.st.round, node["id"])
        if self.st.ai[key]["tries"] > self._retry_limit(node):
            return self._ai_escalate(tid, node, None, f"the output was rejected {self.st.ai[key]['tries']} time(s)",
                                     errors, bump=True)
        attempt = self._bump_attempt(tid, node)
        return self._ai_step(tid, node, attempt)

    # ---- leaving a node: branching -------------------------------------------------------------
    def _leave(self, tid, node):
        tok = self.st.tokens[tid]
        fr = tok["frames"][-1] if tok["frames"] else None
        if fr and node["id"] == fr["body"][fr["pos"]]:
            return self._body_next(tid, fr)
        outs = self.outs.get(node["id"], [])
        if not outs:
            raise Suspend("error", f"{node['id']} has no outgoing transition")
        kind, picked, meta = self._decide(tid, node, outs)
        if kind == "ticket":
            if node["type"] != "task":
                self.emit({"ev": "enter", "tok": tid, "step": node["id"], "round": self.st.round,
                           "attempt": self._attempt(node["id"]), "kind": "choice"})
            attempt = self.st.attempts.get(State.key(self.st.round, node["id"]), 1)
            return self._issue(tid, node, attempt, "choice", candidates=picked)
        if len(picked) > 1:
            return self._fork(tid, node, picked, meta)
        return self._take(tid, node, picked, meta)

    def _cand(self, t):
        return {"transition": t.get("id"), "label": t.get("label") if t.get("label") is not None else
                ("(default)" if t.get("default") else t.get("id")), "to": t["to"],
                "default": bool(t.get("default")), "round_up": _round_up(t)}

    def _decide(self, tid, node, outs):
        tok = self.st.tokens[tid]
        exclusive = node["type"] != "inclusive_choice"
        guarded = [t for t in outs if t.get("guard")]
        plain = [t for t in outs if not t.get("guard") and not t.get("default")]
        defaults = [t for t in outs if t.get("default") and not t.get("guard")]
        hits = [t for t in guarded if self._eval(t["guard"], tok)]
        if hits:
            if exclusive and len(hits) > 1:
                raise Suspend("error", f"{node['id']}: more than one guard is true ({[t.get('id') for t in hits]}); an exclusive branch needs exactly one")
            return "take", hits, {"by": "guard"}
        if node.get("decide_by") and plain:
            val = self._eval(str(node["decide_by"]), tok)
            match = [t for t in plain if str(t.get("label")) == str(val)]
            if match:
                return "take", match[:1] if exclusive else match, {"by": "decide_by", "value": val}
            if defaults:
                return "take", defaults[:1], {"by": "default"}
        is_choice = node["type"] in ("exclusive_choice", "inclusive_choice")
        need_label = (is_choice and plain) or len(plain) > 1 or (plain and defaults)
        if need_label:
            lab = tok.get("label")
            if lab:
                return "take", self._pick_labels(node, lab["label"], exclusive), {"by": "label", "label": lab["label"]}
            return "ticket", [self._cand(t) for t in plain + defaults], None
        if len(plain) == 1:
            return "take", plain, {"by": "single"}
        if defaults:
            if len(defaults) > 1:
                raise Suspend("error", f"{node['id']}: more than one default transition")
            return "take", defaults, {"by": "default"}
        if guarded:
            raise Suspend("error", f"{node['id']}: no guard is true and there is no default transition")
        raise Suspend("error", f"{node['id']}: no transition can be taken")

    def _pick_labels(self, node, label, exclusive=True):
        outs = [t for t in self.outs.get(node["id"], []) if not t.get("guard")]
        want = [str(label).strip()] if exclusive else [x.strip() for x in str(label).split(",")]
        picked = []
        for w in want:
            m = [t for t in outs if str(t.get("label")) == w or t.get("id") == w
                 or (t.get("default") and w in ("(default)", "default"))]
            if not m:
                raise Refused(f"label {w!r} is not a candidate of {node['id']} "
                              f"(candidates: {[self._cand(t)['label'] for t in outs]})")
            picked.append(m[0])
        for t in picked:
            if _round_up(t) and self.st.round + 1 > self.max_rounds():
                raise Refused(f"label {self._cand(t)['label']!r} would start round {self.st.round + 1}, above the ceiling "
                              f"({self.max_rounds()}); choose another candidate")
        return picked

    def max_rounds(self):
        ceil = int(self.cv("limits.max_rounds", 3))
        ex = (self.ir.get("execution") or {}).get("max_rounds")
        return min(ceil, int(ex)) if isinstance(ex, int) and not isinstance(ex, bool) else ceil

    def _take(self, tid, node, trs, meta):
        t = trs[0]
        if _round_up(t):
            if self.st.round + 1 > self.max_rounds():
                raise Suspend("limit", f"{node['id']}: going back would start round {self.st.round + 1}, above the ceiling {self.max_rounds()}")
            self.emit({"ev": "round", "round": self.st.round + 1, "from": node["id"]})
        self.emit({"ev": "transition", "tok": tid, "from": node["id"], "to": t["to"], "transition": t.get("id"),
                   "by": (meta or {}).get("by")})

    # ---- parallel / join ------------------------------------------------------------------------
    def _fork(self, tid, node, trs, meta):
        limit = int(self.cv("limits.parallel", 4))
        active = sum(1 for t in self.st.tokens.values() if not t["queued"]) - 1
        children, n = [], self.st.tok_n
        for i, t in enumerate(trs):
            n += 1
            children.append({"tok": f"t{n}", "to": t["to"], "transition": t.get("id"), "queued": (active + i + 1) > limit})
        self.emit({"ev": "fork", "tok": tid, "from": node["id"], "children": children,
                   "frames": self.st.tokens[tid]["frames"], "by": (meta or {}).get("by")})

    def _join(self, tid, node):
        tok = self.st.tokens[tid]
        if tok["joined"]:
            return self._leave(tid, node)
        need = sorted({t.get("id") or t["from"] for t in self.ins.get(node["id"], [])})
        mode = node.get("join_mode") or (node.get("def") or {}).get("mode") or "all"
        key = f"{node['id']}/{self.st.round}"
        if mode == "any" and self.st.joins.get(key) == ["__fired__"]:
            return self.emit({"ev": "join_drop", "tok": tid, "join": node["id"]})
        self.emit({"ev": "join_arrive", "tok": tid, "join": node["id"], "source": tok.get("via") or tok["from"], "round": self.st.round})
        got = set(self.st.joins.get(key, []))
        if mode == "any" or got >= set(need):
            self.emit({"ev": "join_fire", "join": node["id"], "round": self.st.round, "mode": mode,
                       "tok": f"t{self.st.tok_n + 1}", "frames": tok["frames"], "arrived": sorted(got)})

    # ---- loop -----------------------------------------------------------------------------------
    def _loop(self, tid, node):
        tok = self.st.tokens[tid]
        fr = tok["frames"][-1] if tok["frames"] else None
        limit = min(int(node.get("max_iterations") or 1), int(self.cv("limits.max_iterations", 20)))
        body = list(node.get("body") or [])
        if not (fr and fr["loop"] == node["id"]):
            if not body or any(self.nodes.get(b, {}).get("type") not in ("task", "loop") for b in body):
                raise Suspend("error", f"{node['id']}: a loop body may contain only task / loop steps")
            if node.get("foreach"):
                items = self._eval(str(node["foreach"]), tok)
                items = [] if items is None else items
                if not isinstance(items, list):
                    raise Suspend("error", f"{node['id']}.foreach does not evaluate to a list")
                self.emit({"ev": "loop_start", "tok": tid, "loop": node["id"], "mode": "foreach", "as": node.get("as") or "item",
                           "items": items[:limit], "limit": limit, "limit_reached": len(items) > limit, "body": body})
            else:
                self.emit({"ev": "loop_start", "tok": tid, "loop": node["id"], "mode": "while", "as": node.get("as"),
                           "items": None, "limit": limit, "limit_reached": False, "body": body})
            return None
        idx = len(fr["results"])
        go, item, reached = False, None, fr["limit_reached"]
        if fr["mode"] == "foreach":
            if idx < len(fr["items"]):
                go, item = True, fr["items"][idx]
        else:
            truth = bool(self._eval(str(node.get("while") or "false"), tok))
            if idx >= fr["limit"]:
                reached = truth
            elif truth:
                go, item = True, idx
        if go:
            self.emit({"ev": "loop_iter", "tok": tid, "loop": node["id"], "index": idx, "item": item, "to": body[0]})
            return None
        res = fr["results"]
        self.emit({"ev": "loop_end", "tok": tid, "loop": node["id"], "limit_reached": reached,
                   "result": {"count": len(res), "results": res, "all_passed": all(r == "ok" for r in res),
                              "limit_reached": reached}})
        if reached:
            ex = self.ir.get("execution") or {}
            target = node.get("on_limit") or ex.get("task_error_target")
            if not target or target not in self.nodes:
                raise Suspend("limit", f"{node['id']}: the loop reached its iteration limit and no on_limit / task_error_target is defined")
            return self._take(tid, node, [{"id": None, "from": node["id"], "to": target}], {"by": "loop_limit"})
        return self._leave(tid, node)

    def _body_next(self, tid, fr, failed=False):
        if not failed and fr["pos"] + 1 < len(fr["body"]):
            return self.emit({"ev": "body_next", "tok": tid, "pos": fr["pos"] + 1, "to": fr["body"][fr["pos"] + 1]})
        self.emit({"ev": "iter_end", "tok": tid, "loop": fr["loop"], "result": "failed" if failed else "ok"})

    # ---- finishing ------------------------------------------------------------------------------
    def _run_finally(self):
        for sid in self.ir.get("finally") or []:
            if sid in self.st.finals:
                continue
            node = self.nodes.get(sid)
            self.emit({"ev": "final", "step": sid})
            if not node or node["type"] != "task" or (node.get("kind") or "code") != "code":
                self.emit({"ev": "final_skipped", "step": sid, "note": "only code steps run in finally"})
                continue
            lines = P.lines_of(node)
            if not lines:
                self.progress.append({"step": sid, "result": "ok", "noop": True, "finally": True})
                continue
            attempt = self._attempt(sid)
            self.emit({"ev": "enter", "tok": "finally", "step": sid, "round": self.st.round, "attempt": attempt, "kind": "code"})
            values = self._vars(None)
            cls = self._classify(node, values)
            if any(c["kind"] != "local" for c in cls):
                self.emit({"ev": "final_skipped", "step": sid, "note": "lines are not launchable by the engine"})
                continue
            res = self._exec(node, attempt, cls, values)
            self.emit({"ev": "step_end", "tok": "finally", "step": sid, "round": self.st.round, "attempt": attempt,
                       "result": "ok" if res["ok"] else "error", "outputs": None, "why": res["why"],
                       "evidence": res["evidence"], "finally": True})
            self.progress.append({"step": sid, "result": "ok" if res["ok"] else "error", "finally": True, **res["summary"]})

    def _final_outputs(self):
        """Each declared output: the latest step output carrying it, else a context variable, else an input."""
        out = {}
        order = [d["step"] for d in self.st.done][::-1]
        for name in (self.ir.get("interface") or {}).get("outputs") or {}:
            for sid in order:
                o = self.st.outputs.get(sid)
                if isinstance(o, dict) and name in o:
                    out[name] = o[name]
                    break
            else:
                if name in self.st.ctx:
                    out[name] = self.st.ctx[name]
                elif name in self.st.inputs:
                    out[name] = self.st.inputs[name]
        return out

    def _finish(self, cancelled=False):
        self._run_finally()
        outs = self.st.outcomes
        status = "cancelled" if cancelled else "failed" if any(o in FAILED_OUTCOMES for o in outs) else "done"
        R.write_json(self.rd.file("outputs.json"), self.mask.mask_obj(self._final_outputs()))
        self.emit({"ev": "run_end", "status": status, "outcomes": outs,
                   "raised": self.st.raised if status == "failed" else None})

    # ---- suspend / resume / cancel -------------------------------------------------------------------
    def suspend(self, reason, note):
        if self.st.status in ENDED or self.st.status == "suspended":
            return
        for t in list(self.st.pending()):
            self.emit({"ev": "ticket_discard", "ticket": t["ticket"], "reason": f"suspend:{reason}"})
        self.emit({"ev": "suspend", "reason": reason, "note": note})
        if reason != "child":
            self._suspend_children(f"parent {self.rd.run_id} suspended ({reason})")

    def cmd_suspend(self, note="manual"):
        if self.st.status in ENDED:
            raise Refused(f"the run has ended ({self.st.status})")
        self._lock()
        try:
            self.suspend("manual", note)
        finally:
            self.refresh_run()
            self._unlock()
        return self.describe()

    def same_version_problem(self):
        """Why the run may not resume as-is (definition / implementation changed), or None."""
        want = self.run.get("version_hashes") or {}
        if want.get("impl_sha256") != R.tree_sha256(self.impl_root):
            return "the implementation directory changed since the run started (resume works on the same version only; `resume --migrate` moves it to the new version when that is safe)"
        if self.repo is not None:
            ir, _ = compile_sop(self.run["sop"], self.repo)
            if ir is None or R.sha256_json(ir.to_dict()) != want.get("ir_sha256"):
                return "the procedure definition changed since the run started (resume works on the same version only; `resume --migrate` moves it to the new version when that is safe)"
        return None

    def _refuse_if_child(self, what):
        par = self.run.get("parent")
        if par:
            root = self.root_id()
            raise Refused(f"run {self.rd.run_id} is a child of {par['run_id']} (call {par['step']}); a child cannot be "
                          f"{what} on its own: {what.split()[0]} the root run {root} (children are handled with it)")

    def _resume_problems(self):
        """Why this run (or a suspended child below it) may not resume as-is."""
        out = []
        why = self.same_version_problem() if self.st.status == "suspended" else None
        if why:
            out.append(f"{self.rd.run_id}: {why}")
        for cid in self.active_children():
            out += self._child_engine(cid)._resume_problems()
        return out

    def _resume_inner(self, takeover):
        """Resume the children first (deepest first), then this run, then advance it."""
        for cid in self.active_children():
            ceng = self._child_engine(cid)
            if ceng.st.status == "suspended":
                ceng._lock(takeover=takeover)
                try:
                    ceng._resume_inner(takeover)
                finally:
                    ceng.refresh_run()
                    ceng._unlock()
        if self.st.status == "suspended":
            self.emit({"ev": "resume", "reason": (self.st.suspend or {}).get("reason") if self.st.suspend else None,
                       "owner": self.owner})
        self.advance()

    def _migrate_tree(self, takeover):
        """Plan the move for this run and its active children; apply only if every one of them can move."""
        plans = []

        def collect(eng):
            plans.append((eng, MG.plan(eng)))
            for cid in eng.active_children():
                collect(eng._child_engine(cid))
        collect(self)
        why = [f"{e.rd.run_id}: {w}" for e, p in plans for w in p["why"]]
        if why:
            raise Refused("cannot migrate to the new version:\n  - " + "\n  - ".join(why))
        for e, p in plans:
            if p["unchanged"]:
                continue
            if e is not self:
                e._lock(takeover=takeover)
            try:
                MG.apply(e, p["ir"])
                self.migrated.append({"run_id": e.rd.run_id, "version": p["ir"]["sop"]["version"]})
            finally:
                if e is not self:
                    e.refresh_run()
                    e._unlock()

    def cmd_resume(self, takeover=False, migrate=False):
        if self.st.status != "suspended":
            raise Refused(f"only a suspended run can be resumed (status: {self.st.status})")
        self._refuse_if_child("resumed")
        if not migrate:
            why = self._resume_problems()
            if why:
                raise Refused("; ".join(why))
        elif self.call_repo() is None:
            raise Refused("--migrate needs the procedure root (--sop-root)")
        self._lock(takeover=takeover)
        try:
            if migrate:
                self._migrate_tree(takeover)
                why = self._resume_problems()
                if why:
                    raise Refused("; ".join(why))
            self._resume_inner(takeover)
        finally:
            self.refresh_run()
            self._unlock()
        return self.describe()

    def cmd_cancel(self, reason="cancelled"):
        if self.st.status != "suspended":
            raise Refused("only a suspended run can be cancelled (suspend it first)")
        self._refuse_if_child("cancelled")
        self._lock(takeover=False)
        try:
            self._cancel_children(reason)
            self.emit({"ev": "cancel", "reason": reason})
            self._finish(cancelled=True)
        finally:
            self.refresh_run()
            self._unlock()
        return self.describe()

    # ---- commands: next / submit -----------------------------------------------------------------------
    def cmd_next(self):
        if self.st.status not in ENDED and self.st.status != "suspended":
            self.queue.reap()
            self._lock()
            try:
                self._check_human_timeout()
                self.advance()
            finally:
                self.refresh_run()
                self._unlock()
            self._propagate_up()
        return self.describe()

    def _check_human_timeout(self):
        secs = cfg.duration_seconds(self.cv("human_wait.timeout", "8h"))
        if not secs or self.st.status != "running":
            return
        for t in self.st.pending():
            if t["type"] != "human":
                continue
            ts = R.parse_iso(t.get("ts"))
            if ts and (self.now() - ts).total_seconds() > secs:
                self.suspend("timeout", f"{t['step']}: no decision within {self.cv('human_wait.timeout')}")
                return

    def _find_ticket(self, step, ticket):
        pend = self.st.pending()
        if ticket:
            pend = [t for t in pend if t["ticket"] == ticket]
        if step:
            pend = [t for t in pend if t["step"] == step]
        if len(pend) > 1:
            raise Usage(f"several tickets are pending ({[t['ticket'] for t in pend]}); give --step or --ticket")
        return pend[0] if pend else None

    def cmd_submit(self, step=None, ticket=None, result="ok", outputs=None, label=None, decision=None, by=None, reason=None,
                   raise_=None):
        if self.st.status in ENDED:
            raise Refused(f"the run has ended ({self.st.status})")
        if self.st.status == "suspended":
            raise Refused("the run is suspended; resume it first (pending tickets were discarded)")
        if result not in ("ok", "error"):
            raise Usage("--result must be ok or error")
        if raise_ is not None and (result != "error" or not EXC_RE.match(str(raise_))):
            raise Usage("--raise <ExceptionType> needs --result error and a type name (letters, digits, _)")
        self._lock()
        try:
            t = self._find_ticket(step, ticket)
            if t is None:
                done = [x for x in self.st.tickets.values() if (not step or x["step"] == step) and (not ticket or x["ticket"] == ticket)]
                if done and (step or ticket):
                    return {**self.describe(), "recorded_already": True}
                hit = self._first_child_ticket(self.describe())
                if hit:
                    raise Refused(f"the pending ticket is on child run {hit[0]}; submit to that run id "
                                  f"(procflow submit {hit[0]} ...), the parents advance by themselves")
                raise Refused("no pending ticket" + (f" for {step}" if step else ""))
            try:
                self._submit_ticket(t, result, dict(outputs or {}), label, decision, by, reason, raise_)
            except Suspend as sp:
                self.suspend(sp.reason, sp.note)
            self.advance()
        finally:
            self.refresh_run()
            self._unlock()
        self._propagate_up()
        return self.describe()

    def _submit_ticket(self, t, result, outputs, label, decision, by, reason, raise_=None):
        tid, node, typ = t["tok"], self.nodes[t["step"]], t["type"]
        attempt = t["attempt"]
        if raise_ is not None and typ in ("choice", "human"):
            raise Usage(f"{t['step']}: --raise is for work / command tickets, not {typ} tickets")
        if typ == "choice":
            if not label:
                raise Usage(f"{t['step']}: a choice ticket needs --label (candidates: {[c['label'] for c in t['candidates']]})")
            self._pick_labels(node, label, node["type"] != "inclusive_choice")  # validates; raises Refused
            self.emit({"ev": "label", "tok": tid, "step": node["id"], "label": label, "by": by, "reason": reason})
            self.emit({"ev": "ticket_done", "ticket": t["ticket"]})
            return
        if typ == "human":
            if not decision:
                raise Usage(f"{t['step']}: a human ticket needs --decision <value>")
            preview = f"step {node['id']}: {self.mask.mask_text(str(node.get('title') or ''))}"
            if not self.source.confirm(self.rd.run_id, node["id"], decision, preview):
                self.audit.deny("the approval source did not confirm the decision", action="human_decision",
                                run=self.rd.run_id, step=node["id"], decision=str(decision)[:60])
                self.emit({"ev": "decision_refused", "step": node["id"], "decision": str(decision)[:60]})
                raise DecisionRefused(f"{node['id']}: the approval source did not confirm decision {decision!r}; "
                                      "a decision is never self-approved")
            self.audit.allow(action="human_decision", run=self.rd.run_id, step=node["id"], decision=str(decision)[:60], by=by)
            self.emit({"ev": "decision", "tok": tid, "step": node["id"], "decision": decision, "by": by})
            outs = {"decision": decision}
            declared = list(node.get("outputs") or [])
            if declared:
                outs[declared[0]] = decision
            if t.get("escalated") and outputs:      # a person doing an escalated llm / agent step supplies its outputs
                if node.get("kind") in ("llm", "agent"):
                    try:
                        schema, _ = A.schema_for(self.impl_root, node)
                    except A.AiStepError as x:
                        raise Refused(str(x)) from x
                    bad = A.check_output(outputs, schema, node, impl_root=self.impl_root, context=self._ctx_view(tid),
                                         workdir=self.rd.step_dir(self.st.round, node["id"]),
                                         extra_rule=self.domain_checks.get(node["id"]))
                    if bad:
                        raise Refused(f"{node['id']}: the supplied outputs are not acceptable: {bad[:3]}")
                outs.update(outputs)
            if not label and any(str(x.get("label")) == str(decision) for x in self.outs.get(node["id"], [])):
                label = decision
            if label:
                self.emit({"ev": "label", "tok": tid, "step": node["id"], "label": label, "by": by})
            self.emit({"ev": "ticket_done", "ticket": t["ticket"]})
            self._step_end(tid, node, attempt, "ok", outs, {"decision": decision, "by": by}, keep_all=True)
            self.progress.append({"step": node["id"], "result": "ok", "decision": decision})
            return
        # work / command tickets
        declared = list(node.get("outputs") or [])
        ai_ticket = typ == "work" and node.get("kind") in ("llm", "agent")
        if ai_ticket and t.get("queued"):
            self._require_claimant(t, by)
        if result == "ok":
            missing = [k for k in declared if k not in outputs]
            if missing:
                raise Refused(f"{t['step']}: declared outputs missing: {missing} (submit them with --output name=value)")
        if ai_ticket and result == "ok":
            errors = self._ai_submitted(tid, node, t, outputs, by)
            if errors:
                self.emit({"ev": "ticket_done", "ticket": t["ticket"], "rejected": True})
                self._ai_rejected(tid, node, errors)
                return
        if label:
            self.emit({"ev": "label", "tok": tid, "step": node["id"], "label": label, "by": by})
        self.emit({"ev": "ticket_done", "ticket": t["ticket"]})
        ed = self.rd.step_dir(t["round"], node["id"])
        ev = [R.write_evidence(ed, "submitted_outputs.json", json.dumps(self.mask.mask_obj(outputs), ensure_ascii=False), self.mask)]
        self._step_end(tid, node, attempt, result, outputs if result == "ok" else None, {"evidence": ev, "by": by})
        self.progress.append({"step": node["id"], "result": result})
        if result == "error":
            if raise_ is not None:
                self._record_raise(tid, node, raise_, "step", reason)
            self._after_error(tid, node)

    # ---- records ------------------------------------------------------------------------------------
    def display_status(self):
        st = self.st
        if st.status == "running" and (st.pending() or self._child_waiting()):
            return "waiting"
        return st.status

    def _child_waiting(self):
        for cid in self.active_children():
            try:
                if self._child_engine(cid).display_status() == "waiting":
                    return True
            except ProcflowError:
                continue
        return False

    def refresh_run(self):
        st, run = self.st, self.run
        prev = run.get("status")
        run["status"] = self.display_status()
        run["round"] = st.round
        run["current"] = sorted({t["at"] for t in st.tokens.values()})
        run["suspend"] = st.suspend
        if st.calls:
            run["children"] = [c["child"] for c in st.calls]
            R.write_json(self.rd.file("calls.json"), st.calls)
        if st.status in ENDED:
            run["raised"] = st.raised if st.status == "failed" else None
            run["ended"] = run.get("ended") or R.iso(self.now())
            run["outcome"] = (st.outcomes[-1] if st.outcomes else None) if st.status == "done" else st.status
        hist = run.setdefault("history", [])
        if prev != run["status"]:
            hist.append({"ts": R.iso(self.now()), "status": run["status"],
                         "reason": (st.suspend or {}).get("reason")})
            if run["status"] in ENDED:
                self.tracer.event("run_end", run_id=self.rd.run_id, status=run["status"])
        self.rd.write_run(run)
        if prev != run["status"]:
            R.append_index(self.rd.runs_root, run)

    def describe(self):
        st = self.st
        pend = st.pending()
        out = {"run_id": self.rd.run_id, "sop": self.run["sop"], "status": self.display_status(), "round": st.round,
               "pending": len(pend), "progress": self.progress, "ticket": None, "suspend": st.suspend,
               "outcome": self.run.get("outcome"),
               "queued": bool(pend) and all(x.get("queued") for x in pend)}
        kids = []
        for cid in self.active_children():
            try:
                kd = self._child_engine(cid).describe()
            except ProcflowError:
                continue
            kids.append({k: kd.get(k) for k in ("run_id", "sop", "status", "pending", "ticket", "suspend", "children")})
        if kids:
            out["children"] = kids
        if self.run.get("parent"):
            out["parent"] = self.run["parent"]["run_id"]
            out["root"] = self.root_view or {"run_id": self.root_id()}
        if self.run.get("raised"):
            out["raised"] = self.run["raised"]
        if self.migrated:
            out["migrated"] = self.migrated
        if pend:
            t = pend[0]
            doc = R.read_json(self.rd.path / t["path"]) if t.get("path") else {}
            out["ticket"] = {**doc, "path": t.get("path")}
        return out

    def exit_code(self, d=None):
        s = (d or self.describe())["status"]
        return {"suspended": EXIT_SUSPENDED, "failed": EXIT_REFUSED}.get(s, EXIT_OK)


# ======================================================================================
# helpers
# ======================================================================================
def _walk(v, rest):
    for p in rest:
        if isinstance(v, dict):
            v = v.get(p)
        else:
            return None
    return v


def _round_up(t):
    return any("round" in str(x) for x in (t.get("on_take") or []))


def _in_window(rng, t):
    try:
        a, b = rng.split("-")
        ha, ma = map(int, a.split(":"))
        hb, mb = map(int, b.split(":"))
    except (ValueError, AttributeError):
        return False
    cur, lo, hi = t.hour * 60 + t.minute, ha * 60 + ma, hb * 60 + mb
    return lo <= cur < hi if lo <= hi else (cur >= lo or cur < hi)


def new_run_id(now):
    return f"{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


# ======================================================================================
# starting a run
# ======================================================================================
def create_run(repo, sop_id, *, runs_root=None, env=None, config_file=None, sets=None, params=None, run_id=None,
               owner=None, approval=None, tracer=None, impl_root=None, skill_roots=None, clock=None,
               allow_same_input=False, advance=True, providers=None, domain_checks=None):
    """Start-time checks, then snapshot and first advance. Returns (engine, description).

    Raises Refused (check failed / concurrency / duplicate input / quiet window), Usage (bad inputs) or
    ConfigError (a layer is invalid). Nothing is created on disk when a check fails.
    """
    clock = clock or R.now
    t0 = clock()
    findings = Checker(repo).check_sop(sop_id) + Checker(repo).check_runnable(sop_id)
    if findings:
        raise Refused(f"{sop_id}: the check failed (not runnable): " + "; ".join(findings[:5]))
    ir, errs = compile_sop(sop_id, repo)
    if ir is None:
        raise Refused("; ".join(errs))
    res = cfg.resolve(ir, repo, env=env, config_file=config_file, sets=sets, params=params, now=t0)
    if res["input_errors"]:
        raise Usage("; ".join(res["input_errors"]))
    values = {k: v["value"] for k, v in res["values"].items()}
    runs_root = Path(runs_root or values.get("evidence.root") or "runs")
    rng = values.get("quiet_window.range")
    if rng and _in_window(rng, t0):
        raise Refused(f"inside the quiet window ({rng}); runs are not started now")
    run_id = run_id or new_run_id(t0)
    if not R.valid_run_id(run_id):
        raise Usage("run_id may only use letters, digits and . _ ~ - (no separators, no '..')")
    inputs = {k: x["value"] for k, x in res["inputs"].items()}
    inputs = {k: (run_id if v == "<decided when the run starts>" else v) for k, v in inputs.items()}
    inputs_sha = R.sha256_json(inputs)
    live = [e for e in R.read_index(runs_root) if e.get("status") not in ENDED and not e.get("parent")]
    active = [e for e in live if e.get("status") == "running"]
    cap = int(values.get("limits.max_active_runs") or 3)
    if len(active) >= cap:
        raise Refused(f"{len(active)} runs are already active (limits.max_active_runs={cap})")
    if not allow_same_input and any(e.get("sop") == sop_id and e.get("inputs_sha256") == inputs_sha for e in live):
        raise Refused(f"an unfinished run of {sop_id} with the same inputs already exists")
    impl = Path(impl_root) if impl_root else Path(repo.root) / "_impl" / sop_id
    rd = R.RunDir(runs_root, sop_id, run_id)
    if rd.exists():
        raise Refused(f"run {run_id} already exists")
    reg, rerrs = REG.load(repo.config_dir)
    if rerrs:
        raise Refused("the llm / agent registry is invalid: " + "; ".join(rerrs[:5]))
    ird = ir.to_dict()
    unrouted = RT.findings(ird, res["values"], reg)
    if unrouted:
        raise Refused(f"{sop_id}: an llm / agent step has no execution target: " + "; ".join(unrouted[:3]))
    R.write_json(rd.file("ir.json"), ird)
    REG.pin(rd, reg)
    snap = cfg.mask_result(res)
    R.write_json(rd.file("config.snapshot.json"), {k: snap[k] for k in ("sop", "env", "values", "inputs", "files", "locked", "caps")})
    R.write_json(rd.file("inputs.json"), _MASK.mask_obj(inputs))
    run = {"run_id": run_id, "sop": sop_id, "version": ir.sop.version, "status": "running", "env": res["env"],
           "created": R.iso(t0), "ended": None, "outcome": None, "round": 1, "current": [ir.start],
           "inputs_sha256": inputs_sha, "impl_root": str(impl), "skill_roots": [str(x) for x in (skill_roots or [])],
           "repo": CH.repo_cfg(repo), "raised": None, "depth": 0, "call_path": [sop_id],
           "version_hashes": {"ir_sha256": R.sha256_json(ird), "impl_sha256": R.tree_sha256(impl),
                              "source_sha256": repo.source_sha256(sop_id)},
           "suspend": None, "history": [{"ts": R.iso(t0), "status": "running", "reason": None}]}
    rd.write_run(run)
    eng = Engine(rd, owner=owner, approval=approval, tracer=tracer, impl_root=impl, skill_roots=skill_roots,
                 clock=clock, repo=repo, providers=providers, domain_checks=domain_checks)
    ctx0 = {k: copy.deepcopy(v.get("init")) for k, v in (ird.get("context") or {}).items() if isinstance(v, dict) and "init" in v}
    eng._lock()
    try:
        eng.emit({"ev": "start", "tok": "t1", "node": ir.start, "inputs": _MASK.mask_obj(inputs), "ctx": ctx0,
                  "sop": sop_id, "version": ir.sop.version})
        R.append_index(runs_root, run)
        eng.tracer.event("run_start", run_id=run_id, sop=sop_id)
        if advance:
            eng.advance()
    finally:
        eng.refresh_run()
        eng._unlock()
    return eng, eng.describe()


def open_engine(runs_root, run_id, repo=None, **kw):
    rd = R.find_run(runs_root, run_id)
    return Engine(rd, repo=repo, **kw)
