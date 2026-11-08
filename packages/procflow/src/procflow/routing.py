"""Resolving the execution target of an llm / agent step: granularity first, layer second.

Granularity (the first one that has a value wins)::

    1 step      the step's own ``llm.service`` / ``agent.agent`` in the definition (layer 3) and the config key
                ``steps.<id>.target`` (any layer); the highest layer wins inside this granularity
    2 sop       ``llm.default`` / ``agents.default`` (per-procedure default)
    3 routing   ``needs.<capability>`` of the routing table (agents), then ``<task_class>.<difficulty>``
    4 builtin   the built-in ``session`` target (a work ticket for a worker session)

The chosen spec yields an ordered candidate list (a named target, or every registered target matching ``class=`` /
``requires`` in routing-table order then cheapest first; then the spec's ``fallback`` names). Each candidate is
filtered by: registered, enabled, ``ai.allowed``, data class (``never`` / not accepted / **local-only => family
local only**), required capabilities, model, and -- when the run is unattended -- ``ai.unattended_allowed``
(a ``session`` target is exempt: it queues a ticket instead of being called). The first one that passes is the
target; the others that pass are the runtime fallback order. When none passes ``target`` is None (``check`` flags
it, run creation refuses, mid-run the engine suspends with ``limit``) -- except for a local-only class, where the
route carries ``to_human`` and the engine hands the step to a person instead (data of that class never leaves).
"""

from __future__ import annotations

from . import registry as REG

PARAM_KEYS = ["effort", "temperature", "timeout", "max_tokens"]


def _val(values, key):
    v = (values or {}).get(key)
    if isinstance(v, dict) and v.get("value") is not None:
        return v["value"], v.get("layer", 1), v.get("source", "")
    return None, None, None


def profile(node, reg):
    kind = node.get("kind")
    d = node.get("def") or {}
    blk = d.get(kind) if kind in ("llm", "agent") and isinstance(d.get(kind), dict) else {}
    return {"kind": kind, "task_class": blk.get("task_class"), "difficulty": blk.get("difficulty") or "medium",
            "data_class": blk.get("data_class") or reg.default_class,
            "needs": [str(x) for x in blk.get("needs") or []], "block": blk}


def _norm(v, kind):
    """A string or map -> {"name", "model", "cls", "requires", "params", "fallback"} (None when empty)."""
    if v is None:
        return None
    if isinstance(v, str):
        parts = [x.strip() for x in v.split(",") if x.strip()]
        if not parts:
            return None
        first = parts[0]
        if first.startswith("class="):
            spec = {"name": None, "model": None, "cls": first[6:].strip(), "requires": [], "params": {}, "fallback": []}
        else:
            _, name, model = REG.parse_target(first, kind)
            spec = {"name": name, "model": model, "cls": None, "requires": [], "params": {}, "fallback": []}
        spec["fallback"] = parts[1:]
        return spec
    if not isinstance(v, dict):
        return None
    key = "service" if kind == "llm" else "agent"
    name, model = v.get(key), v.get("model")
    if isinstance(name, str) and ("/" in name or ":" in name):
        _, name, m2 = REG.parse_target(name, kind)
        model = model or m2
    spec = {"name": name, "model": model, "cls": v.get("class"), "requires": [str(x) for x in v.get("requires") or []],
            "params": {k: v[k] for k in PARAM_KEYS if k in v}, "fallback": [str(x) for x in v.get("fallback") or []]}
    if not spec["name"] and not spec["cls"] and not spec["requires"]:
        spec["overlay_only"] = True
    return spec


def _best(cands):
    best = None
    for c in cands:
        if c[2] is not None and (best is None or c[0] >= best[0]):
            best = c
    return best


def resolve(node, values, reg, unattended=None):
    """The route of one llm / agent step: a dict (``target`` None when nothing passes)."""
    pf = profile(node, reg)
    kind = "agent" if pf["kind"] == "agent" else "llm"
    sid = node["id"]
    if unattended is None:
        unattended = bool(_val(values, "run.unattended")[0])
    chosen, overlays = None, []
    # 1 step
    cands = []
    if pf["block"]:
        sp = _norm(pf["block"], kind)
        if sp is not None:
            cands.append((3, f"definition {sid}.{pf['kind']}", sp))
    v, layer, src = _val(values, f"steps.{sid}.target")
    if v is not None:
        cands.append((layer, src, _norm(v, kind)))
    full = [c for c in cands if c[2] and not c[2].get("overlay_only")]
    overlays += [c for c in cands if c[2] and c[2].get("overlay_only")]
    best = _best(full)
    if best:
        chosen = ("step", best)
    # 2 sop
    if chosen is None:
        v, layer, src = _val(values, "llm.default" if kind == "llm" else "agents.default")
        if v is not None:
            sp = _norm(v, kind)
            if sp and not sp.get("overlay_only"):
                chosen = ("sop", (layer, src, sp))
    if kind == "llm":
        v, layer, src = _val(values, "llm.model")
        if v is not None:
            overlays.append((layer, src, {"model": v, "params": {}}))
    # 3 routing
    if chosen is None:
        rt = reg.routing or {}
        if kind == "agent":
            for cap in pf["needs"]:
                spec = (rt.get("needs") or {}).get(cap)
                if spec:
                    chosen = ("routing", (2, f"routing needs.{cap}", _norm(spec, kind)))
                    break
        if chosen is None and pf["task_class"]:
            sect = "llm" if kind == "llm" else "agents"
            spec = ((rt.get(sect) or {}).get(pf["task_class"]) or {}).get(pf["difficulty"])
            if spec:
                chosen = ("routing", (2, f"routing {sect}.{pf['task_class']}.{pf['difficulty']}", _norm(spec, kind)))
    # 4 builtin
    if chosen is None:
        chosen = ("builtin", (1, "built-in session target", _norm(REG.BUILTIN_TARGET, kind)))
    gran, (layer, source, spec) = chosen
    route = {"step": sid, "kind": kind, "step_kind": pf["kind"], "task_class": pf["task_class"],
             "difficulty": pf["difficulty"], "data_class": pf["data_class"], "needs": pf["needs"],
             "granularity": gran, "layer": f"L{layer}", "source": source, "target": None, "model": None,
             "provider": None, "family": None, "params": {}, "fallback": [], "candidates": [], "unattended": unattended}
    allowed = _names(values, "ai.allowed")
    ua = (_names(values, "ai.unattended_allowed") or []) if unattended else None
    for name, model in _candidates(spec, kind, reg, pf):
        why = viable(reg, kind, name, model, pf, allowed, ua)
        route["candidates"].append({"id": REG.target_id(kind, name, model), "why": why})
        if why:
            continue
        ent = reg.entry(kind, name)
        m = model or ent.get("default_model")
        if route["target"] is None:
            route.update(target=name, model=m, provider=ent["provider"], family=ent["family"])
        else:
            route["fallback"].append({"target": name, "model": m})
    params = dict((spec or {}).get("params") or {})
    for _l, _s, ov in sorted(overlays, key=lambda x: x[0]):
        params.update(ov.get("params") or {})
        if ov.get("model") and route["target"]:
            models = reg.entry(kind, route["target"]).get("models") or []
            if models and ov["model"] in models:
                route["model"] = ov["model"]
    route["params"] = params
    if route["target"] is None and reg.is_local_only(pf["data_class"]):
        route["to_human"] = (f"data class {pf['data_class']} never leaves; no local target is registered, enabled and "
                             "allowed, so the step goes to a person")
    return route


def _names(values, key):
    v = _val(values, key)[0]
    return set(v) if isinstance(v, list) else None


def _order(reg, kind, pf):
    sect = "llm" if kind == "llm" else "agents"
    tbl = (reg.routing or {}).get(sect) or {}
    key = "service" if kind == "llm" else "agent"
    order = []
    for tc in ([pf["task_class"]] if pf["task_class"] else []) + list(tbl):
        for df in REG.DIFFICULTIES:
            n = ((tbl.get(tc) or {}).get(df) or {}) if isinstance((tbl.get(tc) or {}).get(df), dict) else {}
            n = n.get(key)
            if n and n not in order:
                order.append(n)
    return order


def _candidates(spec, kind, reg, pf):
    out = []
    if spec is None:
        return out
    if spec.get("name"):
        out.append((spec["name"], spec.get("model")))
    else:
        table = reg.services if kind == "llm" else reg.agents
        order = _order(reg, kind, pf)
        matched = [n for n, e in table.items()
                   if (not spec.get("cls") or spec["cls"] in (e.get("classes") or []))
                   and set(spec.get("requires") or []) <= set(e.get("capabilities") or [])
                   and not e.get("builtin")]
        matched.sort(key=lambda n: (order.index(n) if n in order else len(order), table[n].get("cost_per_call") or 0, n))
        out += [(n, None) for n in matched]
    for fb in spec.get("fallback") or []:
        k, n, m = REG.parse_target(fb, kind)
        out.append((n, m))
    seen, uniq = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def viable(reg, kind, name, model, pf, allowed=None, unattended_allowed=None):
    """Why a candidate cannot be used (a sentence), or None."""
    ent = reg.entry(kind, name)
    what = "llm service" if kind == "llm" else "agent"
    if ent is None:
        return f"{what} {name} is not registered"
    if not ent.get("enabled"):
        return f"{what} {name} is disabled ({ent.get('disabled_reason') or 'no reason given'})"
    if allowed is not None and name not in allowed:
        return f"{what} {name} is outside ai.allowed"
    dc = pf["data_class"]
    if dc not in reg.classes:
        return f"unknown data class {dc}"
    if reg.is_never(dc):
        return f"data class {dc} is never passed anywhere"
    if reg.is_local_only(dc) and ent.get("family") != "local":
        return f"data class {dc} is local-only; {what} {name} is not a local target"
    if dc not in (ent.get("data_classes") or []):
        return f"{what} {name} does not accept data class {dc}"
    caps = ent.get("capabilities") or []
    if kind == "agent" and "*" not in caps:
        miss = [c for c in pf["needs"] if c not in caps]
        if miss:
            return f"agent {name} lacks the required capabilities {miss}"
    models = ent.get("models") or []
    if model and models and model not in models:
        return f"{what} {name} has no model {model} (known: {models})"
    if unattended_allowed is not None and ent.get("provider") != "session" and name not in unattended_allowed:
        return f"{what} {name} cannot be called directly in an unattended run (outside ai.unattended_allowed)"
    return None


def is_ai_node(node):
    return node.get("type") == "task" and node.get("kind") in ("llm", "agent")


def describe(route):
    if not route.get("target"):
        return "a person (local-only data never leaves)" if route.get("to_human") else "(no target)"
    s = REG.target_id(route["kind"], route["target"], route.get("model"))
    if route.get("fallback"):
        s += "  fallback-> " + ", ".join(REG.target_id(route["kind"], f["target"], f.get("model")) for f in route["fallback"])
    return s


def routes(ir, values, reg, unattended=None):
    """[route] for every llm / agent step of an IR dict."""
    return [resolve(n, values, reg, unattended) for n in ir["nodes"] if is_ai_node(n)]


def findings(ir, values, reg, unattended=None):
    """Messages for steps that have no target (and no human fallback)."""
    out = []
    for r in routes(ir, values, reg, unattended):
        if r["target"] is None and not r.get("to_human"):
            why = "; ".join(c["why"] for c in r["candidates"] if c["why"]) or "no candidate"
            out.append(f"{r['step']}: no execution target passes the routing filters ({why})")
    return out


def rows(ir, values, reg):
    """Rows for ``config show --steps``: step, kind, target, granularity, layer/source, note."""
    out = []
    for n in ir["nodes"]:
        if n.get("type") != "task":
            continue
        k = n.get("kind")
        if is_ai_node(n):
            r = resolve(n, values, reg)
            note = []
            if r["target"] is None and not r.get("to_human"):
                note.append("; ".join(c["why"] for c in r["candidates"] if c["why"]))
            elif r.get("to_human") or reg.is_local_only(r["data_class"]):
                note.append(f"{r['data_class']}: falls to a person when no local target is usable")
            out.append((n["id"], k, describe(r), r["granularity"], f"{r['layer']} {r['source']}", " ".join(note)))
        else:
            out.append((n["id"], k or "-", "a person" if k == "human" else "engine", "-", "-", ""))
    return out


def render_rows(sop_id, table):
    head = ("step", "kind", "target", "granularity", "layer / source", "note")
    allr = [head] + table
    ws = [max(len(str(r[i])) for r in allr) for i in range(5)]
    lines = [f"# execution targets of the llm / agent steps of {sop_id} (no run was created)"]
    for r in allr:
        lines.append("  ".join(str(r[i]).ljust(ws[i]) for i in range(5)) + "  " + str(r[5]))
    return "\n".join(lines)
