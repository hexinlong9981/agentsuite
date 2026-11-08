"""Definition (dict) -> IR. No execution.

- resolves ``extends``: an override replaces the base step; steps only the child
  has are inserted after the step that precedes them in the child's order;
  ``super: before|after`` splices the base step in as ``<id>-super``
- derives each task's ``kind`` (explicit ``kind`` else from ``mode``)
- synthesizes ``interface`` from ``flow.inputs/outputs`` and ``context`` from step
  outputs when they are not declared (``origin: derived``)
- inlines the interface contract (``interface_spec``) into interface-call nodes

A definition without extension keys compiles to nodes == steps and transitions 1:1.
Findings are returned (not raised); static validation across procedures is check.py.
"""

from __future__ import annotations

import copy

from .defschema import BUILTIN_EXCEPTIONS, EXTENDS_RE, IMPLEMENTS_RE, MODE_DEFAULT_KIND, OVERRIDE_RE
from .ir import IR, IRCall, IRInterface, IRNode, IRSop, IRTransition
from .model import InterfaceSpec, as_list

SUPER_SUFFIX = "-super"
_TRUE = {True, "true", "True", "required", "yes"}

NODE_COPY = {
    "start": ["condition"],
    "end": ["outcome"],
    "task": ["actor", "mode", "inputs", "outputs", "evidence", "tools", "io", "impl", "retry", "llm", "agent", "super"],
    "exclusive_choice": ["decide_by"],
    "inclusive_choice": ["decide_by"],
    "parallel_split": ["branches"],
    "join": ["wait_for"],
    "loop": ["foreach", "while", "as", "max_iterations", "body", "on_limit"],
    "call": ["sop", "interface", "dispatch", "with", "returns", "max_depth", "allow_recursion", "catch",
             "reason", "with_config"],
}


def merge_extends(child, lookup):
    """Resolve ``extends``. ``lookup(id)`` returns a base definition dict or None.

    Returns (merged, errs, info); ``info`` = {parent_id, parent, origin{step: procedure},
    overrides{child-step: base-step dict}, replaced{base-step-id: child-step-id},
    runtime_layers[(id, runtime)]}.
    """
    sop = child.get("sop") or {}
    sid = sop.get("id")
    info = {"parent_id": None, "parent": None, "origin": {}, "overrides": {}, "replaced": {},
            "runtime_layers": [(sid, child.get("runtime"))] if child.get("runtime") is not None else []}
    for s in child.get("steps") or []:
        if isinstance(s, dict):
            info["origin"][s.get("id")] = sid
    ext = sop.get("extends")
    errs = []
    if not ext:
        for s in child.get("steps") or []:
            if isinstance(s, dict) and "override" in s:
                errs.append(f"{s.get('id')}: override may only be written on a procedure that extends a base")
        return child, errs, info
    m = EXTENDS_RE.match(str(ext)) if isinstance(ext, str) else None
    if not m:
        return child, errs, info  # malformed: reported by defschema
    pid, major = m.group(1), m.group(2)
    info["parent_id"] = pid
    if pid == sid:
        return child, [f"sop.extends names the procedure itself ({pid})"], info
    parent = lookup(pid)
    if parent is None:
        return child, [f"base procedure {pid} does not exist"], info
    info["parent"] = parent
    psop = parent.get("sop") or {}
    if psop.get("abstract") is not True:
        errs.append(f"base {pid} is not abstract: only an abstract procedure can be extended")
    if psop.get("extends"):
        errs.append(f"base {pid} itself extends {psop.get('extends')}: inheritance is a single level")
    if str(psop.get("version", "")).split(".")[0] != major:
        errs.append(f"sop.extends major {major} does not match base {pid} version {psop.get('version')}")

    psteps = [s for s in parent.get("steps") or [] if isinstance(s, dict)]
    pmap = {s.get("id"): s for s in psteps}
    by_target = {}
    for cs in child.get("steps") or []:
        if not isinstance(cs, dict):
            continue
        ov = cs.get("override")
        if ov is not None:
            om = OVERRIDE_RE.match(str(ov))
            if not om:
                continue
            if om.group(1) != pid:
                errs.append(f"{cs.get('id')}: override base {om.group(1)} differs from extends {pid}")
                continue
            tgt = om.group(2)
            if tgt not in pmap:
                errs.append(f"{cs.get('id')}: override target {pid}.{tgt} does not exist")
                continue
            if tgt in by_target:
                errs.append(f"{cs.get('id')}: {pid}.{tgt} is already overridden by {by_target[tgt].get('id')}")
                continue
            by_target[tgt] = cs
        elif cs.get("id") in pmap:
            errs.append(f"{cs.get('id')}: base {pid} has a step with the same id; to replace it write "
                        f"override: {pid}.{cs.get('id')} explicitly (same-name shadowing is an error)")

    steps = []
    for ps in psteps:
        step_id = ps.get("id")
        if step_id in by_target:
            cs = by_target[step_id]
            node = copy.deepcopy(ps)
            for k in ("abstract", "hook"):
                node.pop(k, None)
            node.update(copy.deepcopy(cs))
            steps.append(node)
            info["overrides"][cs.get("id")] = ps
            info["replaced"][step_id] = cs.get("id")
            info["origin"][cs.get("id")] = sid
        else:
            steps.append(copy.deepcopy(ps))
            info["origin"][step_id] = pid
    # child-only steps: after the step that precedes them in the child's order (else first)
    prev = None
    for cs in child.get("steps") or []:
        if not isinstance(cs, dict):
            continue
        if cs.get("override") is not None or cs.get("id") in pmap:
            if any(s.get("id") == cs.get("id") for s in steps):
                prev = cs.get("id")
            continue
        pos = 0
        if prev is not None:
            pos = next((i + 1 for i, s in enumerate(steps) if s.get("id") == prev), len(steps))
        steps.insert(pos, cs)
        prev = cs.get("id")

    rep = info["replaced"]
    ctrs = {t.get("id"): t for t in child.get("transitions") or [] if isinstance(t, dict)}
    trs = []
    for pt in parent.get("transitions") or []:
        if not isinstance(pt, dict):
            continue
        if pt.get("id") in ctrs:
            if pt.get("final"):
                errs.append(f"transition {pt.get('id')} is final in base {pid} and cannot be overridden")
            continue
        t = copy.deepcopy(pt)
        t["from"], t["to"] = rep.get(t.get("from"), t.get("from")), rep.get(t.get("to"), t.get("to"))
        trs.append(t)
    trs += [t for t in child.get("transitions") or [] if isinstance(t, dict)]

    merged = copy.deepcopy(child)
    merged["steps"], merged["transitions"] = steps, trs
    if "flow" not in child and "flow" in parent:
        merged["flow"] = copy.deepcopy(parent["flow"])
    if "interface" not in child and "interface" in parent:
        merged["interface"] = copy.deepcopy(parent["interface"])
    if "context" in parent:
        ctx = copy.deepcopy(parent["context"]) if isinstance(parent["context"], dict) else {}
        ctx.update(child.get("context") or {})
        merged["context"] = ctx
    fin = [rep.get(x, x) for x in as_list(parent.get("finally"))] + as_list(child.get("finally"))
    if fin:
        merged["finally"] = list(dict.fromkeys(fin))
    if parent.get("runtime") is not None:
        info["runtime_layers"].insert(0, (pid, parent.get("runtime")))
    errs += _expand_super(merged, info)
    return merged, errs, info


def _expand_super(merged, info):
    """Splice the base step in as ``<id>-super`` before/after an overriding step."""
    errs = []
    steps, trs = merged["steps"], merged["transitions"]
    for cid, pstep in list(info["overrides"].items()):
        cs = next((s for s in steps if s.get("id") == cid), None)
        how = (cs or {}).get("super")
        if how not in ("before", "after"):
            continue
        if pstep.get("abstract"):
            errs.append(f"{cid}: super cannot call the abstract base step {info['parent_id']}.{pstep.get('id')} (it has no body)")
            continue
        sid_ = f"{cid}{SUPER_SUFFIX}"
        node = copy.deepcopy(pstep)
        node["id"], node["super_of"] = sid_, cid
        node.pop("final", None)
        idx = next(i for i, s in enumerate(steps) if s.get("id") == cid)
        steps.insert(idx if how == "before" else idx + 1, node)
        info["origin"][sid_] = info["parent_id"]
        if how == "before":
            for t in trs:
                if t.get("to") == cid:
                    t["to"] = sid_
            trs.append({"id": sid_, "from": sid_, "to": cid})
        else:
            for t in trs:
                if t.get("from") == cid:
                    t["from"] = sid_
            trs.append({"id": sid_, "from": cid, "to": sid_})
    return errs


def derive_kind(step):
    if step.get("kind"):
        return step["kind"]
    k = MODE_DEFAULT_KIND.get(step.get("mode"))
    if k == "agent" and "llm" in step:
        return "llm"
    return k


def derive_interface(d) -> IRInterface:
    """Declared ``interface`` or one synthesized from flow.inputs / flow.outputs (types ``any``)."""
    itf = d.get("interface")
    flow = d.get("flow") or {}
    if isinstance(itf, dict):
        out = IRInterface(inputs=copy.deepcopy(itf.get("inputs") or {}),
                          outputs=copy.deepcopy(itf.get("outputs") or {}),
                          raises=list(itf.get("raises") or []), origin="declared")
        if "raises" not in itf:
            out.raises, out.raises_origin = _collect_raises(d), "derived"
        return out
    out = IRInterface(origin="derived")
    for i in flow.get("inputs") or []:
        if isinstance(i, dict) and i.get("id"):
            out.inputs[str(i["id"])] = {"type": "any", "required": i.get("required") in _TRUE,
                                        "from": f"flow.inputs.{i['id']}"}
    for o in flow.get("outputs") or []:
        if isinstance(o, dict) and o.get("id"):
            out.outputs[str(o["id"])] = {"type": "any", "required": True}
    out.raises, out.raises_origin = _collect_raises(d), "derived"
    return out


def _collect_raises(d):
    rs = set()
    for s in d.get("steps") or []:
        if isinstance(s, dict):
            rs |= set(as_list(s.get("raises")))
    return sorted(rs)


def derive_context(d):
    """Names -> {type, init?, origin}. Step outputs are derived as ``any``; declared entries win."""
    ctx = {}
    for s in d.get("steps") or []:
        if isinstance(s, dict):
            for o in as_list(s.get("outputs")):
                ctx[str(o)] = {"type": "any", "origin": "derived"}
    if isinstance(d.get("context"), dict):
        for k, v in d["context"].items():
            spec = dict(v) if isinstance(v, dict) else {}
            spec["origin"] = "declared"
            ctx[str(k)] = spec
    return ctx


def build_ir(d, info, path=None, sha256=None) -> IR:
    sop = d.get("sop") or {}
    steps = [s for s in d.get("steps") or [] if isinstance(s, dict)]
    nodes = []
    for s in steps:
        typ = s.get("type")
        n = IRNode(id=s.get("id"), type=typ, origin=info["origin"].get(s.get("id"), sop.get("id")))
        if s.get("id") in info["overrides"]:
            n.override = s.get("override")
        n.super_of = s.get("super_of")
        for k in NODE_COPY.get(typ, []):
            if k in s:
                n.props[k] = copy.deepcopy(s[k])
        if typ == "join" and "mode" in s:
            n.props["join_mode"] = s["mode"]
        if typ == "task":
            n.kind = derive_kind(s)
            n.title = s.get("name") or s.get("action")
            n.abstract = bool(s.get("abstract"))
            n.hook = bool(s.get("hook"))
            n.visibility = s.get("visibility") or ("protected" if (n.abstract or n.hook) else "private")
            n.raises = as_list(s.get("raises"))
        n.final = bool(s.get("final"))
        n.definition = copy.deepcopy(s)
        nodes.append(n)
    trs = []
    for t in d.get("transitions") or []:
        if isinstance(t, dict):
            trs.append(IRTransition(id=t.get("id"), src=t.get("from"), dst=t.get("to"), label=t.get("when"),
                                    guard=t.get("guard"), default=bool(t.get("default")),
                                    on_take=copy.deepcopy(t.get("on_take")), data=copy.deepcopy(t.get("data")),
                                    final=bool(t.get("final"))))
    abstract_steps = [n.id for n in nodes if n.abstract]
    flow = d.get("flow") or {}
    calls = [IRCall(step=n.id, sop=n.get("sop"), interface=n.get("interface"),
                    dispatch=[x.get("sop") for x in n.get("dispatch") or [] if isinstance(x, dict)])
             for n in nodes if n.type == "call"]
    return IR(
        sop=IRSop(id=sop.get("id"), name=sop.get("name"), version=str(sop.get("version")),
                  declared_abstract=sop.get("abstract") is True,
                  abstract=sop.get("abstract") is True or bool(abstract_steps), abstract_steps=abstract_steps,
                  extends=sop.get("extends"), implements=as_list(sop.get("implements")),
                  human_only=as_list(sop.get("human_only")), source=str(path) if path else None, sha256=sha256),
        interface=derive_interface(d), context=derive_context(d),
        runtime_layers=[[o, copy.deepcopy(r)] for o, r in info["runtime_layers"]],
        start=flow.get("start"), execution=copy.deepcopy(flow.get("execution") or {}),
        finally_=as_list(d.get("finally")), nodes=nodes, transitions=trs, calls=calls,
        raw_flow=copy.deepcopy(flow), builtin_exceptions=list(BUILTIN_EXCEPTIONS))


def compile_definition(d, lookup, interface_lookup=None, path=None, sha256=None):
    """Compile a definition dict. Returns (IR, errs)."""
    merged, errs, info = merge_extends(d, lookup)
    ir = build_ir(merged, info, path, sha256)
    for n in ir.nodes:
        if n.type == "call" and n.get("interface") and interface_lookup is not None:
            m = IMPLEMENTS_RE.match(str(n.get("interface")))
            spec = interface_lookup(m.group(1)) if m else None
            if spec is not None:
                n.interface_spec = InterfaceSpec.from_dict(spec).to_dict()
    ir.merge = {"parent_id": info["parent_id"], "replaced": dict(info["replaced"]),
                "overrides": sorted(info["overrides"])}
    return ir, errs


def compile_sop(sop_id, repo):
    """Compile ``sop_id`` from a Repo. Returns (IR | None, errs). Validation is separate (check.py)."""
    try:
        d = repo.definition(sop_id)
    except KeyError:
        return None, [f"procedure {sop_id} does not exist"]

    def lookup(pid):
        try:
            return repo.definition(pid)
        except KeyError:
            return None

    return compile_definition(d, lookup, repo.interface, repo.path(sop_id), repo.source_sha256(sop_id))
