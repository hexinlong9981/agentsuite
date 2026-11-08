"""Static checks across procedures. Every finding is an English sentence; tests match on its reason.

Per procedure, in order:
  1. closed key set + value rules (defschema) and basic structure: required keys, ids, reachability
  2. compile (extends: base exists and is abstract, single level, major match, explicit override, final transitions)
  3. OOP: implementation gaps, override LSP (final / private / human / raises / evidence / outputs),
     human_only not reduced, parent<->child interface compatibility, implements contracts and
     cross-interface same-name type conflicts, super not on an abstract base
  4. control: loop bodies, finally, calls (target exists / not abstract, required ``with``, ``returns``,
     ``catch``, dispatch implementations or default_impl, mapped types, narrowing-only ``with_config``),
     call cycles (every call on a cycle needs allow_recursion + max_depth), call-chain depth cap
  5. expressions: guard / decide_by / dispatch / with references rooted in declared names; ``${config.<key>}`` exists
  6. human fixity: a human/manual step is never silently automated
  7. runtime: the L3 ``runtime:`` block against schema/runtime_keys.yaml
``check_runnable`` additionally refuses abstract procedures (``procflow check --run``).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config as cfg
from . import guard
from .compiler import compile_sop, derive_kind
from .defschema import (BUILTIN_EXCEPTIONS, HARD_MAX_DEPTH, IMPLEMENTS_RE, VERSION_RE, check_definition,
                        check_interface_file, type_compatible, value_matches)

GUARD_ROOTS = {"round", "limits", "retry", "timeout", "config", "env", "run", "item", "human_wait", "params"}


@dataclass(frozen=True)
class Finding:
    """One structured finding. ``target`` is a procedure id or a file path."""

    target: str
    message: str
    severity: str = "error"

    def to_dict(self):
        return {"target": self.target, "severity": self.severity, "message": self.message}


def _step_text(node):
    d = node.definition or {}
    return " ".join(str(d.get(k) or "") for k in ("name", "action"))


def _evidence(step):
    if isinstance(step, dict):
        return [str(x) for x in step.get("evidence") or []]
    return []


class Checker:
    def __init__(self, repo):
        self.repo = repo
        self._ir = {}

    def ir(self, sop_id):
        if sop_id not in self._ir:
            self._ir[sop_id] = compile_sop(sop_id, self.repo)
        return self._ir[sop_id]

    def _raw(self, sop_id):
        try:
            return self.repo.definition(sop_id)
        except KeyError:
            return {}

    # ---- one procedure --------------------------------------------------------------
    def check_sop(self, sop_id):
        if self.repo.path(sop_id) is None:
            return [f"procedure {sop_id} does not exist"]
        raw = self.repo.definition(sop_id)
        errs = list(check_definition(raw))
        errs += self.check_required(raw)
        ir, cerrs = self.ir(sop_id)
        errs += [x for x in cerrs if x not in errs]
        if ir is None:
            return errs
        errs += self.check_structure(ir)
        errs += self.check_oop(ir)
        errs += self.check_control(ir)
        errs += self.check_exprs(ir)
        errs += self.check_runtime(ir)
        errs += self.check_cycles(sop_id)
        errs += self.check_depth(sop_id)
        return list(dict.fromkeys(errs))

    # ---- required keys, ids, reachability ---------------------------------------------
    def check_required(self, raw):
        errs = []
        sop = raw.get("sop") if isinstance(raw.get("sop"), dict) else None
        if sop is None:
            return ["required key sop is missing"]
        for k in ("id", "name", "version"):
            if not sop.get(k):
                errs.append(f"required key sop.{k} is missing")
        if sop.get("version") is not None and not VERSION_RE.match(str(sop.get("version"))):
            errs.append(f"sop.version must be major.minor")
        if not isinstance(raw.get("steps"), list) or not raw.get("steps"):
            errs.append("required key steps is missing or empty")
        return errs

    def check_structure(self, ir):
        errs = []
        e = errs.append
        ids = [n.id for n in ir.nodes]
        seen = set()
        for i in ids:
            if i in seen:
                e(f"duplicate step id {i}")
            seen.add(i)
        tids = set()
        for t in ir.transitions:
            if not t.id:
                e("a transition has no id")
            elif t.id in tids:
                e(f"duplicate transition id {t.id}")
            tids.add(t.id)
            for end, name in ((t.src, "from"), (t.dst, "to")):
                if end not in seen:
                    e(f"transition {t.id}: {name} {end!r} is not a step")
        for n in ir.nodes:
            if n.type == "task" and not str((n.definition or {}).get("action") or "").strip():
                e(f"{n.id}: a task needs action")
        if ir.sop.abstract:
            return errs  # partial definitions have no start/end of their own; checked on the concrete child
        starts = [n for n in ir.nodes if n.type == "start"]
        if not ir.start:
            e("required key flow.start is missing")
        elif ir.node(ir.start) is None or ir.node(ir.start).type != "start":
            e(f"flow.start {ir.start!r} must be a step of type start")
        if len(starts) != 1:
            e(f"exactly one start step is required (found {len(starts)})")
        if not any(n.type == "end" for n in ir.nodes):
            e("at least one end step is required")
        adj = {}
        for t in ir.transitions:
            adj.setdefault(t.src, []).append(t.dst)
        for n in ir.nodes:
            if n.type == "loop":
                adj.setdefault(n.id, []).extend(n.get("body") or [])
                if n.get("on_limit"):
                    adj.setdefault(n.id, []).append(n.get("on_limit"))
            if n.type == "call":
                adj.setdefault(n.id, []).extend((n.get("catch") or {}).values())
        roots = [ir.start] if ir.start else []
        roots += list(ir.finally_)
        reach, stack = set(), [r for r in roots if r in seen]
        while stack:
            u = stack.pop()
            if u in reach:
                continue
            reach.add(u)
            stack.extend(v for v in adj.get(u, []) if v in seen)
        for n in ir.nodes:
            if n.id not in reach and n.id not in ir.finally_:
                e(f"{n.id} is unreachable from flow.start")
        return errs

    # ---- OOP ---------------------------------------------------------------------------
    def check_oop(self, ir):
        errs = []
        e = errs.append
        s = ir.sop
        own_abstract = [n.id for n in ir.nodes if n.abstract]
        if own_abstract and not s.declared_abstract:
            e(f"implementation gap: abstract steps {own_abstract} remain (override and implement them, "
              f"or declare sop.abstract: true; as is it cannot run)")
        pid = ir.merge.get("parent_id")
        parent = None
        if pid:
            try:
                parent = self.repo.definition(pid)
            except KeyError:
                parent = None
        if parent:
            pho = [str(x) for x in (parent.get("sop") or {}).get("human_only") or []]
            missing = [x for x in pho if x not in s.human_only]
            if missing:
                e(f"LSP: human_only {missing} of base {pid} was dropped (human-only parts cannot be reduced)")
            pmap = {st.get("id"): st for st in parent.get("steps") or [] if isinstance(st, dict)}
            for ptid, cid in (ir.merge.get("replaced") or {}).items():
                ps, cn = pmap.get(ptid), ir.node(cid)
                if ps and cn:
                    errs += self._lsp_step(pid, ps, cn)
        for imp in s.implements:
            errs += self._check_implements(ir, imp)
        errs += self._implements_conflicts(s.implements)
        if parent:
            errs += self._lsp_interface(pid, parent, ir)
        return errs

    def _implements_conflicts(self, imps):
        errs, seen = [], {}
        for imp in imps:
            m = IMPLEMENTS_RE.match(imp)
            itf = self.repo.interface(m.group(1)) if m else None
            if not itf:
                continue
            for part in ("inputs", "outputs"):
                for name, spec in (itf.get(part) or {}).items():
                    t = (spec or {}).get("type", "any")
                    key = (part, name)
                    if key in seen and not (type_compatible(t, seen[key][1]) and type_compatible(seen[key][1], t)):
                        errs.append(f"implements conflict: {seen[key][0]} and {m.group(1)} declare {part}.{name} with different "
                                    f"types ({seen[key][1]} vs {t}); both cannot be implemented")
                    seen.setdefault(key, (m.group(1), t))
        return errs

    def _lsp_interface(self, pid, parent, ir):
        pitf = parent.get("interface")
        citf = self._raw(ir.sop.id).get("interface")
        if not isinstance(pitf, dict) or not isinstance(citf, dict):
            return []
        errs = []
        w = f"LSP (interface of base {pid})"
        pin, cin = pitf.get("inputs") or {}, citf.get("inputs") or {}
        for name, sp in pin.items():
            sp = sp or {}
            if name not in cin:
                errs.append(f"{w}: input {name} is no longer accepted (a child accepts every parent input)")
            elif not type_compatible(sp.get("type", "any"), (cin[name] or {}).get("type", "any")):
                errs.append(f"{w}: input {name} type {(cin[name] or {}).get('type')} cannot accept parent type {sp.get('type')}")
        for name, sp in cin.items():
            sp = sp or {}
            if sp.get("required") and "default" not in sp and not ((pin.get(name) or {}).get("required")):
                errs.append(f"{w}: input {name} is optional (or absent) in the parent but required here; callers cannot supply it")
        pout, cout = pitf.get("outputs") or {}, citf.get("outputs") or {}
        for name, sp in pout.items():
            if name not in cout:
                errs.append(f"{w}: output {name} is no longer produced (the output contract cannot weaken)")
            elif not type_compatible((cout[name] or {}).get("type", "any"), (sp or {}).get("type", "any")):
                errs.append(f"{w}: output {name} type {(cout[name] or {}).get('type')} does not match parent {(sp or {}).get('type')}")
        if "raises" in pitf:
            extra = [x for x in citf.get("raises") or [] if x not in (pitf.get("raises") or []) and x not in BUILTIN_EXCEPTIONS]
            if extra:
                errs.append(f"{w}: raises {extra} not declared by the parent (exceptions cannot grow)")
        return errs

    def _lsp_step(self, pid, ps, cn):
        errs = []
        e = errs.append
        where = f"{cn.id} (override {pid}.{ps.get('id')})"
        if ps.get("final"):
            e(f"{where}: the base step is final and cannot be overridden")
        pvis = ps.get("visibility") or ("protected" if (ps.get("abstract") or ps.get("hook")) else "private")
        if pvis == "private":
            e(f"{where}: the base step is private (only protected abstract/hook steps can be overridden)")
        pkind = derive_kind(ps)
        if pkind == "human" and cn.kind != "human":
            e(f"{where}: LSP: a human step cannot be overridden to kind {cn.kind} (human steps are fixed; automating one is refused)")
        extra = [x for x in cn.raises if x not in (ps.get("raises") or []) and x != "SystemError"]
        if extra:
            e(f"{where}: LSP: raises {extra} not declared by the base step (exceptions cannot grow)")
        lost = [x for x in _evidence(ps) if x not in _evidence(cn.definition)]
        if lost:
            e(f"{where}: LSP: base evidence {lost} was dropped")
        pout = [str(x) for x in ps.get("outputs") or []]
        cout = [str(x) for x in (cn.definition or {}).get("outputs") or []]
        lost = [x for x in pout if x not in cout]
        if lost:
            e(f"{where}: LSP: base outputs {lost} are no longer produced (the output contract cannot weaken)")
        return errs

    def _check_implements(self, ir, imp):
        errs = []
        e = errs.append
        m = IMPLEMENTS_RE.match(imp)
        if not m:
            return errs
        iid, major = m.group(1), m.group(2)
        itf = self.repo.interface(iid)
        if itf is None:
            return [f"implements: interface {iid} not found in {self.repo.interfaces_dir.name}/"]
        if str(itf.get("version", "")).split(".")[0] != major:
            e(f"implements: {imp} major does not match {iid} version {itf.get('version')}")
        mine = ir.interface
        w = f"implements {iid}"
        for name, spec in (itf.get("inputs") or {}).items():
            spec = spec or {}
            mspec = (mine.inputs or {}).get(name)
            if mspec is None:
                if spec.get("required"):
                    e(f"{w}: LSP: required input {name} of the interface is not accepted")
                continue
            if not type_compatible(spec.get("type", "any"), mspec.get("type", "any")):
                e(f"{w}: LSP: input {name} type {mspec.get('type')} cannot accept the interface type {spec.get('type')}")
        for name, mspec in (mine.inputs or {}).items():
            if mspec.get("required") and "default" not in mspec:
                ispec = (itf.get("inputs") or {}).get(name)
                if ispec is None or not ispec.get("required"):
                    e(f"{w}: LSP: input {name} is required here but not (or optional) in the interface; callers cannot supply it")
        for name, spec in (itf.get("outputs") or {}).items():
            spec = spec or {}
            mspec = (mine.outputs or {}).get(name)
            if mspec is None:
                if spec.get("required", True):
                    e(f"{w}: LSP: output {name} of the interface is not produced")
                continue
            if not type_compatible(mspec.get("type", "any"), spec.get("type", "any")):
                e(f"{w}: LSP: output {name} type {mspec.get('type')} does not match the interface type {spec.get('type')}")
        allowed = set(itf.get("raises") or []) | set(BUILTIN_EXCEPTIONS)
        raised = set(mine.raises or [])
        for n in ir.nodes:
            raised |= set(n.raises or [])
        extra = sorted(raised - allowed)
        if extra:
            e(f"{w}: LSP: raises {extra} not declared by the interface")
        humans = [n for n in ir.nodes if n.kind == "human"]
        for phrase in (itf.get("safety") or {}).get("human_steps_required") or []:
            hit = any(phrase in _step_text(n) for n in humans) or (phrase in ir.sop.human_only and humans)
            if not hit:
                e(f"{w}: safety: the interface requires a human step for '{phrase}' but none (kind human) covers it")
        return errs

    # ---- control -----------------------------------------------------------------------
    def check_control(self, ir):
        errs = []
        e = errs.append
        ids = {n.id for n in ir.nodes}
        for x in ir.finally_:
            if x not in ids:
                e(f"finally step {x} does not exist")
        for n in ir.nodes:
            if n.type == "loop":
                for b in n.get("body") or []:
                    if b not in ids:
                        e(f"{n.id}: loop body step {b} does not exist")
                    elif b == n.id:
                        e(f"{n.id}: loop body contains the loop itself")
                if n.get("on_limit") and n.get("on_limit") not in ids:
                    e(f"{n.id}: on_limit step {n.get('on_limit')} does not exist")
            if n.type == "call":
                errs += self._check_call(ir, n, ids)
            if n.type == "task" and n.kind in ("code", "llm", "agent") and (n.definition or {}).get("kind"):
                text = _step_text(n)
                for phrase in ir.sop.human_only:
                    if str(phrase) and str(phrase) in text:
                        e(f"{n.id}: human_only '{phrase}' is performed by a kind {n.kind} step (human steps are fixed; automating one is refused)")
        itf = ir.interface
        if itf.origin == "declared" and itf.raises_origin != "derived":
            own = set(BUILTIN_EXCEPTIONS) | set(itf.raises or [])
            for n in ir.nodes:
                extra = [x for x in n.raises if x not in own]
                if extra:
                    e(f"{n.id}: raises {extra} is not declared in interface.raises")
        flow_inputs = {str(i["id"]) for i in (ir.raw_flow.get("inputs") or []) if isinstance(i, dict) and i.get("id")}
        for name, spec in (itf.inputs or {}).items():
            fr = (spec or {}).get("from")
            if fr and str(fr).startswith("flow.inputs.") and str(fr)[len("flow.inputs."):] not in flow_inputs:
                e(f"interface.inputs.{name}.from {fr} is not in flow.inputs")
        return errs

    def _check_call(self, ir, n, ids):
        errs = []
        e = errs.append
        sid = n.id
        targets, callee_spec = [], None
        if n.get("sop"):
            targets = [n.get("sop")]
        elif n.get("interface"):
            m = IMPLEMENTS_RE.match(str(n.get("interface")))
            if m:
                itf = self.repo.interface(m.group(1))
                if itf is None:
                    e(f"{sid}: call interface {m.group(1)} does not exist")
                else:
                    if str(itf.get("version", "")).split(".")[0] != m.group(2):
                        e(f"{sid}: call {n.get('interface')} major does not match {m.group(1)} version {itf.get('version')}")
                    callee_spec = itf
                    targets = [x.get("sop") for x in n.get("dispatch") or [] if isinstance(x, dict) and x.get("sop")]
                    if itf.get("default_impl"):
                        targets.append(itf["default_impl"])
                    if not targets:
                        e(f"{sid}: no dispatch and no default_impl for {m.group(1)}: no implementation can be chosen (always NoImplementation)")
                    for t in targets:
                        tir, _ = self.ir(t) if self.repo.path(t) else (None, None)
                        if tir is not None and n.get("interface") not in tir.sop.implements:
                            e(f"{sid}: dispatch target {t} does not implement {n.get('interface')}")
        for t in targets:
            if self.repo.path(t) is None:
                e(f"{sid}: call target {t} does not exist")
                continue
            tir, _ = self.ir(t)
            if tir is None:
                continue
            if tir.sop.abstract:
                e(f"{sid}: call target {t} is abstract (cannot run directly)")
            if callee_spec is None:
                errs += self._check_mapping(sid, n, tir.interface.__dict__, t)
        if callee_spec is not None:
            errs += self._check_mapping(sid, n, callee_spec, n.get("interface"))
        for exc, step in (n.get("catch") or {}).items():
            if step not in ids:
                e(f"{sid}: catch {exc} target {step} does not exist")
        errs += cfg.check_with_config(sid, n.get("with_config"))
        spec = callee_spec
        if spec is None and len(targets) == 1 and self.repo.path(targets[0]) is not None:
            tir, _ = self.ir(targets[0])
            spec = tir.interface.__dict__ if tir is not None else None
        if spec is not None:
            errs += self._check_types(ir, n, spec)
        return errs

    def _decl_type(self, ir, name):
        c = (ir.context or {}).get(name)
        if isinstance(c, dict) and c.get("origin") == "declared" and c.get("type"):
            return c["type"]
        i = (ir.interface.inputs or {}).get(name)
        if isinstance(i, dict) and ir.interface.origin == "declared" and i.get("type"):
            return i["type"]
        return None

    def _check_types(self, ir, n, spec):
        errs = []
        sid = n.id
        ins, outs = spec.get("inputs") or {}, spec.get("outputs") or {}
        for k, v in (n.get("with") or {}).items():
            want = (ins.get(k) or {}).get("type", "any")
            if not isinstance(v, str):
                if not value_matches(v, want):
                    errs.append(f"{sid}: with.{k} value does not fit the child input type {want}")
                continue
            try:
                node = guard.parse(v)
            except guard.GuardError:
                continue
            if node[0] == "lit" and not value_matches(node[1], want):
                errs.append(f"{sid}: with.{k} value does not fit the child input type {want}")
            elif node[0] == "ref" and len(node[1]) == 1:
                have = self._decl_type(ir, node[1][0])
                if have and not type_compatible(have, want):
                    errs.append(f"{sid}: with.{k} ({node[1][0]} has type {have}) does not fit the child input type {want}")
        for k, dest in (n.get("returns") or {}).items():
            have = (outs.get(k) or {}).get("type", "any")
            want = self._decl_type(ir, str(dest))
            if want and not type_compatible(have, want):
                errs.append(f"{sid}: returns {k} (child output type {have}) cannot go into {dest} (type {want})")
        return errs

    def _check_mapping(self, sid, n, spec, name):
        errs = []
        inputs, outputs = spec.get("inputs") or {}, spec.get("outputs") or {}
        given = n.get("with") or {}
        for k, v in inputs.items():
            v = v or {}
            if v.get("required") and "default" not in v and k not in given:
                errs.append(f"{sid}: required input {k} of {name} is not passed in with")
        for k in given:
            if k not in inputs:
                errs.append(f"{sid}: with.{k} is not an input of {name}")
        for k in n.get("returns") or {}:
            if k not in outputs:
                errs.append(f"{sid}: returns {k} is not an output of {name}")
        allowed = set(spec.get("raises") or []) | set(BUILTIN_EXCEPTIONS)
        for exc in n.get("catch") or {}:
            if exc not in allowed:
                errs.append(f"{sid}: catch {exc} is not an exception {name} raises")
        return errs

    # ---- expressions -------------------------------------------------------------------
    def check_exprs(self, ir):
        errs = []
        e = errs.append
        names = {n.id for n in ir.nodes} | set(ir.context) | set(ir.interface.inputs or {}) | GUARD_ROOTS
        names |= {str(i["id"]) for i in (ir.raw_flow.get("inputs") or []) if isinstance(i, dict) and i.get("id")}
        for n in ir.nodes:
            if n.type == "loop" and n.get("as"):
                names.add(n.get("as"))
            if n.type == "call":
                names |= {str(k) for k in (n.get("returns") or {}).values()}
        step_ids = {n.id for n in ir.nodes}

        def chk(where, text):
            try:
                node = guard.parse(str(text))
            except guard.GuardError:
                return  # syntax errors come from defschema
            for r in sorted(guard.refs(node)):
                if r[0] not in names:
                    e(f"{where}: reference {'.'.join(r)} is not declared ({r[0]} is not a step id, context name, input, round, limits, ...)")
                if r[0] == "config" and not cfg.known_key(".".join(r[1:])):
                    e(f"{where}: config key {'.'.join(r[1:])} does not exist in schema/runtime_keys.yaml")

        for t in ir.transitions:
            if t.guard:
                chk(f"transition {t.id}", t.guard)
        for n in ir.nodes:
            if n.get("decide_by"):
                chk(f"{n.id}.decide_by", n.get("decide_by"))
                root = str(n.get("decide_by")).split(".")[0]
                if root not in step_ids:
                    e(f"{n.id}.decide_by must be <step-id>.... ({root} is not a step)")
            if n.type == "loop":
                for k in ("foreach", "while"):
                    if n.get(k):
                        chk(f"{n.id}.{k}", n.get(k))
            if n.type == "call":
                for i, d in enumerate(n.get("dispatch") or []):
                    if isinstance(d, dict) and d.get("when"):
                        chk(f"{n.id}.dispatch[{i}].when", d["when"])
                for k, v in (n.get("with") or {}).items():
                    if isinstance(v, str):
                        chk(f"{n.id}.with.{k}", v)
        for name, spec in (ir.interface.inputs or {}).items():
            if isinstance(spec, dict) and "default" in spec:
                try:
                    kind, arg = guard.parse_default(spec["default"])
                except guard.GuardError:
                    continue
                if kind == "config" and not cfg.known_key(arg):
                    e(f"interface.inputs.{name}.default: config key {arg} does not exist in schema/runtime_keys.yaml")
        return errs

    def check_runtime(self, ir):
        errs = []
        entries, _ = cfg.layer3_from_ir(ir, errs)
        for k, v, src in entries:
            sp = cfg.match_key(k)
            if sp is None:
                continue
            if 3 not in sp.layers:
                errs.append(f"{src}: {k} cannot be written in a definition (allowed layers: {sp.layers})")
                continue
            errs.extend(cfg.check_value(sp, k, v, src))
        return errs

    # ---- call cycles and depth ---------------------------------------------------------
    def _edges(self, sop_id):
        ir, _ = self.ir(sop_id)
        if ir is None:
            return []
        out = []
        for n in ir.nodes:
            if n.type != "call":
                continue
            ts = [n.get("sop")] if n.get("sop") else [x.get("sop") for x in n.get("dispatch") or [] if isinstance(x, dict)]
            if n.get("interface"):
                m = IMPLEMENTS_RE.match(str(n.get("interface")))
                itf = self.repo.interface(m.group(1)) if m else None
                if itf and itf.get("default_impl"):
                    ts.append(itf["default_impl"])
            ok = bool(n.get("allow_recursion")) and n.get("max_depth") is not None
            out += [(t, n.id, ok) for t in ts if t]
        return out

    def check_cycles(self, sop_id):
        """Cycles through ``sop_id``: every call on the cycle needs allow_recursion and max_depth."""
        errs, stack, onpath, seen = [], [], set(), set()

        def dfs(u):
            stack.append(u)
            onpath.add(u)
            for v, step, ok in self._edges(u):
                if self.repo.path(v) is None:
                    continue
                if v == sop_id:
                    cyc = stack[stack.index(v):] + [v]
                    bad = [(a, s) for a, (b, s, o) in self._cycle_edges(cyc) if not o]
                    if bad:
                        errs.append(f"call cycle {' -> '.join(cyc)}: {', '.join(f'{a}.{s}' for a, s in bad)} lacks "
                                    f"allow_recursion: true and max_depth")
                elif v not in onpath and v not in seen:
                    dfs(v)
            onpath.discard(u)
            seen.add(u)
            stack.pop()

        dfs(sop_id)
        return list(dict.fromkeys(errs))

    def _cycle_edges(self, cyc):
        return [(a, (b, step, ok)) for a, b in zip(cyc, cyc[1:]) for v, step, ok in self._edges(a) if v == b]

    def check_depth(self, sop_id):
        """The (acyclic) call chain must not exceed the hard cap of limits.max_depth."""
        memo = {}

        def depth(u, path):
            if u in memo:
                return memo[u]
            best = 0
            for v, _step, _ok in self._edges(u):
                if v in path or self.repo.path(v) is None:
                    continue
                best = max(best, 1 + depth(v, path | {v}))
            memo[u] = best
            return best

        d = depth(sop_id, {sop_id})
        if d > HARD_MAX_DEPTH:
            return [f"call chain depth {d} exceeds the cap {HARD_MAX_DEPTH} (hard cap of limits.max_depth)"]
        return []

    # ---- direct run ----------------------------------------------------------------------
    def check_runnable(self, sop_id):
        ir, errs = self.ir(sop_id)
        if ir is None:
            return errs
        if ir.sop.abstract:
            why = "sop.abstract: true" if ir.sop.declared_abstract else f"abstract steps {ir.sop.abstract_steps} remain"
            return [f"{sop_id} is abstract ({why}) and cannot be run directly; run a concrete procedure that extends and implements it"]
        return []

    # ---- whole root ------------------------------------------------------------------------
    def check_interfaces(self):
        out = []
        import yaml
        for iid, p in self.repo.interface_paths.items():
            try:
                d = yaml.safe_load(p.read_text(encoding="utf-8"))
            except yaml.YAMLError as x:
                out.append((str(p), [f"not readable as YAML: {type(x).__name__}"]))
                continue
            errs = check_interface_file(d, p.name)
            di = d.get("default_impl") if isinstance(d, dict) else None
            if di:
                if self.repo.path(di) is None:
                    errs.append(f"default_impl {di} does not exist")
                else:
                    tir, _ = self.ir(di)
                    want = f"{iid}@{str(d.get('version', '')).split('.')[0]}"
                    if tir is not None and want not in tir.sop.implements:
                        errs.append(f"default_impl {di} does not implement {want}")
                    if tir is not None and tir.sop.abstract:
                        errs.append(f"default_impl {di} is abstract")
            out.append((str(p), errs))
        return out

    def check_routing(self, sop_id):
        """llm / agent steps that no registered, enabled, allowed target can serve (default config)."""
        from . import registry as REG
        from . import routing as RT
        ir, _ = self.ir(sop_id)
        if ir is None or ir.sop.abstract:
            return []
        reg, rerrs = REG.load(self.repo.config_dir)
        if rerrs:
            return []          # reported once under "registry"
        try:
            res = cfg.resolve(ir, self.repo)
        except cfg.ConfigError:
            return []          # reported by the config check
        return RT.findings(ir.to_dict(), res["values"], reg)

    def check_registry(self):
        from . import registry as REG
        reg, errs = REG.load(self.repo.config_dir)
        return [(str(self.repo.config_dir), errs)] if errs else []

    def check_all(self):
        """[(target, [error strings])] for every procedure, interface and config file."""
        out = [(sid, self.check_sop(sid)) for sid in self.repo.ids()]
        out += [(p, [m]) for p, m in self.repo.load_errors]
        out += self.check_interfaces()
        out += cfg.check_config_files(self.repo, self.ir)
        out += self.check_registry()
        return out


def run_check(repo, sop_ids=None, run=False):
    """Structured findings for the whole root (or the given ids). With ``run``, abstract procedures are refused."""
    chk = Checker(repo)
    findings = []
    if sop_ids:
        for sid in sop_ids:
            for m in chk.check_sop(sid):
                findings.append(Finding(sid, m))
            if run:
                for m in chk.check_runnable(sid) + chk.check_routing(sid):
                    findings.append(Finding(sid, m))
    else:
        for target, errs in chk.check_all():
            findings += [Finding(target, m) for m in errs]
        if run:
            for sid in repo.ids():
                findings += [Finding(sid, m) for m in chk.check_routing(sid)]
    return findings
