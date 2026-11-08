"""``resume --migrate``: move a suspended run to a newer version of its definition, only when that is safe.

A run executes off the IR it started with. ``--migrate`` swaps in the current definition's IR when ALL of these hold
(otherwise nothing changes and every reason is listed):

* the new definition passes the static check (and is runnable);
* the procedure's version has the same major, and every interface the run implemented keeps the same major;
* every node the run is standing on -- each token's node, each pending ticket's step, each in-progress loop (with the
  same body) -- exists in the new IR with the same type (and, for a task, the same kind);
* everything the run has recorded can be received: context variables (declared, loop variables or call ``returns``),
  step outputs (a node of that id), and the inputs (declared inputs of the new interface).

The old IR is kept as ``ir.<n>.json`` (n = the migration number), ``run.json`` gets ``migrations`` and a ``migrate`` event is
recorded. A run with child runs moves together with them, planned first so that either all move or none does.
"""

from __future__ import annotations

import os

from . import records as R
from .check import Checker
from .compiler import compile_sop
from .defschema import IMPLEMENTS_RE


def _major(v):
    return str(v).split(".")[0]


def _impl_major(entry):
    m = IMPLEMENTS_RE.match(str(entry))
    return (m.group(1), m.group(2)) if m else (str(entry).split("@")[0], str(entry).split("@")[-1].split(".")[0])


def plan(eng):
    """What migrating ``eng``'s run would do: ``{"unchanged": bool, "ir": dict | None, "why": [reasons]}``."""
    repo = eng.call_repo()
    if repo is None:
        return {"unchanged": False, "ir": None, "why": ["--migrate needs the procedure root (--sop-root)"]}
    sid = eng.run["sop"]
    ch = Checker(repo)
    errs = list(dict.fromkeys(ch.check_sop(sid) + ch.check_runnable(sid)))
    if errs:
        return {"unchanged": False, "ir": None, "why": ["the new definition does not pass the check: " + "; ".join(errs)[:500]]}
    ir, cerrs = compile_sop(sid, repo)
    if ir is None:
        return {"unchanged": False, "ir": None, "why": ["the new definition cannot be compiled: " + "; ".join(cerrs)[:300]]}
    new = ir.to_dict()
    want = eng.run.get("version_hashes") or {}
    from pathlib import Path
    impl = Path(eng.run.get("impl_root") or (Path(repo.root) / "_impl" / sid))
    unchanged = R.sha256_json(new) == want.get("ir_sha256") and R.tree_sha256(impl) == want.get("impl_sha256")
    why = []
    old = eng.ir
    if _major(old["sop"]["version"]) != _major(new["sop"]["version"]):
        why.append(f"the version major differs ({old['sop']['version']} -> {new['sop']['version']}): the interface is "
                   "incompatible, start a new run")
    newi = dict(_impl_major(x) for x in new["sop"].get("implements") or [])
    for x in old["sop"].get("implements") or []:
        k, mj = _impl_major(x)
        if newi.get(k) != mj:
            why.append(f"implements {x} is {'absent' if k not in newi else 'at another major'} in the new version")
    nn = {n["id"]: n for n in new["nodes"]}
    on = {n["id"]: n for n in old["nodes"]}
    st = eng.st
    stand = {t["at"] for t in st.tokens.values()} | {t["step"] for t in st.pending()}
    loops = {}
    for t in st.tokens.values():
        for fr in t.get("frames") or []:
            loops[fr["loop"]] = fr["body"]
    for sidn in sorted(stand | set(loops)):
        if sidn not in nn:
            why.append(f"the run is at {sidn}, which the new version does not have")
            continue
        o = on.get(sidn)
        if o is not None and (nn[sidn]["type"] != o["type"] or (o["type"] == "task" and nn[sidn].get("kind") != o.get("kind"))):
            why.append(f"the run is at {sidn}, whose kind changed ({o['type']}/{o.get('kind')} -> "
                       f"{nn[sidn]['type']}/{nn[sidn].get('kind')})")
        if sidn in loops and list(nn[sidn].get("body") or []) != list(loops[sidn]):
            why.append(f"loop {sidn} is in progress and its body changed")
    names = set(new.get("context") or {}) | {n.get("as") for n in new["nodes"] if n["type"] == "loop" and n.get("as")} | {"item"}
    names |= {str(d) for n in new["nodes"] if n["type"] == "call" for d in (n.get("returns") or {}).values()}
    for k in sorted(st.ctx):
        if k not in names:
            why.append(f"context variable {k} cannot be received by the new version")
    for k in sorted(st.outputs):
        if k not in nn:
            why.append(f"the output of step {k} cannot be received: the new version has no such step")
    for k in sorted(st.inputs):
        if k not in (new.get("interface") or {}).get("inputs", {}):
            why.append(f"input {k} is not an input of the new version")
    return {"unchanged": unchanged, "ir": new, "why": why}


def apply(eng, new):
    """Swap ``eng`` to ``new`` (a plan's IR). The caller holds the run's lease."""
    repo = eng.call_repo()
    sid = eng.run["sop"]
    migs = list(eng.run.get("migrations") or [])
    n = len(migs) + 1
    old_file = f"ir.{n}.json"
    os.replace(eng.rd.file("ir.json"), eng.rd.file(old_file))
    text = R.dumps(new)
    R.write_atomic(eng.rd.file("ir.json"), text)
    from pathlib import Path
    impl = Path(eng.run.get("impl_root") or (Path(repo.root) / "_impl" / sid))
    vh = dict(eng.run.get("version_hashes") or {})
    prev = {"version": eng.run.get("version"), "ir_sha256": vh.get("ir_sha256")}
    eng.run["version"] = new["sop"]["version"]
    eng.run["version_hashes"] = {**vh, "ir_sha256": R.sha256_json(new), "impl_sha256": R.tree_sha256(impl),
                                 "source_sha256": repo.source_sha256(sid)}
    migs.append({"n": n, "from_version": prev["version"], "to_version": new["sop"]["version"],
                 "from_ir_sha256": prev["ir_sha256"], "to_ir_sha256": eng.run["version_hashes"]["ir_sha256"],
                 "old_ir": old_file, "by": eng.owner, "ts": R.iso(eng.now())})
    eng.run["migrations"] = migs
    eng.rd.write_run(eng.run)
    eng.emit({"ev": "migrate", "n": n, "from_version": prev["version"], "to_version": new["sop"]["version"],
              "from_ir_sha256": prev["ir_sha256"], "to_ir_sha256": eng.run["version_hashes"]["ir_sha256"],
              "old_ir": old_file, "by": eng.owner})
    eng.rebind_ir(new)
    eng.tracer.event("migrate", run_id=eng.rd.run_id, sop=sid, count=n)
    return old_file
