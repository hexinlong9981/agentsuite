"""Resource catalogue: what the procedures reference, and whether it still exists.

Typed resource ids::

    kind:<code|llm|agent|human>   a step kind (built in)
    llm:<name>  agent:<name>      a registered llm service / agent (status from the registry: enabled / disabled)
    prog:<relative path>          a program a code step launches (status: the file exists under the impl root)
    tool:<name>                   a tool a step declares (``tools:`` of a task or an agent block); status comes from the
                                  *tool source*, see below
    sop:<ID>                      another procedure a ``call`` step names (status: it exists in the root)

The **tool source** is an external list of what is available -- the catalogue never talks to anything. It is a JSON
file ``{"tools": ["<name>", ...]}`` (or ``{"tools": {"<name>": {...}}}``) named by ``PROCFLOW_CATALOG_SOURCES`` (a
test injects its own through that variable). Without a source a tool is ``unverified`` (not an error); with one, a
tool absent from it is ``missing``.

``build`` writes ``<out>/catalog.json`` (and ``<out>/diff_<time>.json`` against the previous build); ``impact <id>``
lists the procedures / steps that reference a resource; ``check`` exits non-zero when a *referenced* resource is
``missing`` (a ``disabled`` one is only a warning: routing may fall back).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path

from .. import programs as P
from .. import records as R
from .. import registry as REG
from ..compiler import compile_sop

ENV_SOURCES = "PROCFLOW_CATALOG_SOURCES"
STEP_KINDS = ("code", "llm", "agent", "human")
STATUSES = ("available", "disabled", "missing", "unverified")


def _read_source():
    p = os.environ.get(ENV_SOURCES)
    if not p:
        return None, None
    path = Path(p)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, f"the tool source named by {ENV_SOURCES} cannot be read"
    t = d.get("tools") if isinstance(d, dict) else None
    names = set(t) if isinstance(t, dict) else {str(x) for x in t or []}
    return {"names": names, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, None


def _refs_of(node, sop_id, impl_root):
    """[(resource id, type)] a task / call node references."""
    out = []
    d = node.get("def") or {}
    typ = node.get("type")
    if typ == "call":
        if d.get("sop"):
            out.append((f"sop:{d['sop']}", "sop"))
        return out
    if typ != "task":
        return out
    kind = node.get("kind") or "code"
    out.append((f"kind:{kind}", "kind"))
    if kind == "code":
        for ln in P.lines_of(node):
            argv = str(ln).split()
            prog = next((a for a in argv if a.endswith(".py") or "/" in a), None) if argv else None
            if prog and not prog.startswith("<"):
                out.append((f"prog:{prog}", "prog"))
    blk = d.get(kind) if kind in ("llm", "agent") and isinstance(d.get(kind), dict) else {}
    key = "service" if kind == "llm" else "agent"
    for ref in ([blk.get(key)] if blk.get(key) else []) + list(blk.get("fallback") or []):
        _, name, _m = REG.parse_target(ref, kind)
        out.append((f"{kind}:{name}", kind))
    for tl in list(d.get("tools") or []) + list(blk.get("tools") or []):
        out.append((f"tool:{tl}", "tool"))
    return out


def build(repo, impl_root_of=None, config_dir=None):
    """The catalogue dict for every compilable procedure in ``repo``."""
    reg, rerrs = REG.load(config_dir or repo.config_dir)
    src, src_err = _read_source()
    resources, refs, notes = {}, {}, []
    if rerrs:
        notes.append("the registry is invalid: " + "; ".join(rerrs[:3]))
    for k in STEP_KINDS:
        resources[f"kind:{k}"] = {"type": "kind", "name": k, "status": "available", "source": "built-in"}
    for kind, tbl in (("llm", reg.services), ("agent", reg.agents)):
        for name, e in sorted(tbl.items()):
            resources[f"{kind}:{name}"] = {"type": kind, "name": name, "source": "registry",
                                           "status": "available" if e.get("enabled") else "disabled",
                                           "detail": {"provider": e.get("provider"), "family": e.get("family"),
                                                      "data_classes": e.get("data_classes"),
                                                      "reason": e.get("disabled_reason")}}
    if src_err:
        notes.append(src_err)
    for sid in repo.ids():
        ir, errs = compile_sop(sid, repo)
        if ir is None:
            notes.append(f"{sid}: does not compile; its references are not catalogued")
            continue
        impl = impl_root_of(sid) if impl_root_of else Path(repo.root) / "_impl" / sid
        for n in ir.to_dict()["nodes"]:
            for rid, typ in _refs_of(n, sid, impl):
                refs.setdefault(rid, []).append({"sop": sid, "step": n["id"]})
                if rid in resources:
                    continue
                name = rid.split(":", 1)[1]
                if typ == "prog":
                    ok = (Path(impl) / name).is_file() or Path(name).is_absolute() and Path(name).is_file()
                    resources[rid] = {"type": "prog", "name": name, "source": f"impl:{sid}",
                                      "status": "available" if ok else "missing"}
                elif typ == "tool":
                    st = "unverified" if src is None else ("available" if name in src["names"] else "missing")
                    resources[rid] = {"type": "tool", "name": name, "source": "tool-source" if src else "none", "status": st}
                elif typ == "sop":
                    resources[rid] = {"type": "sop", "name": name, "source": "root",
                                      "status": "available" if repo.path(name) else "missing"}
                else:  # llm / agent referenced by name but not registered
                    resources[rid] = {"type": typ, "name": name, "source": "registry", "status": "missing"}
    return {"built_at": R.iso(), "resources": dict(sorted(resources.items())),
            "references": {k: v for k, v in sorted(refs.items())}, "notes": notes,
            "source_sha256": (src or {}).get("sha256")}


def write(cat, out_dir):
    out = Path(out_dir)
    prev = R.read_json(out / "catalog.json")
    R.write_json(out / "catalog.json", cat)
    diff = None
    if prev:
        a, b = prev.get("resources", {}), cat["resources"]
        diff = {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
                "changed": sorted(k for k in set(a) & set(b) if a[k].get("status") != b[k].get("status"))}
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        R.write_json(out / f"diff_{stamp}.json", diff)
    return diff


def load(out_dir):
    return R.read_json(Path(out_dir) / "catalog.json")


def impact(cat, rid):
    """The procedures / steps that reference ``rid`` (empty when unreferenced)."""
    return list((cat.get("references") or {}).get(rid, []))


def check(cat):
    """``(errors, warnings)``: a referenced resource that is missing is an error; a disabled one a warning."""
    errs, warns = [], []
    for rid, who in (cat.get("references") or {}).items():
        r = (cat.get("resources") or {}).get(rid) or {"status": "missing"}
        where = ", ".join(sorted({f"{w['sop']}.{w['step']}" for w in who}))[:200]
        if r["status"] == "missing":
            errs.append(f"{rid} is missing but still referenced by {where}")
        elif r["status"] == "disabled":
            warns.append(f"{rid} is disabled but referenced by {where}")
    return errs, warns


def render_list(cat, rtype=None):
    rows = [(rid, r["status"], r.get("source", "")) for rid, r in cat["resources"].items() if not rtype or r["type"] == rtype]
    w = max([len(r[0]) for r in rows] + [2])
    return "\n".join([f"{rid.ljust(w)}  {st:<11} {src}" for rid, st, src in rows] + [f"{len(rows)} resource(s)"])
