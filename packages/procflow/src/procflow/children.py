"""Child runs of a ``call``: creation (config narrowing, linking) and the call tree helpers.

A child is a real run: its own directory under the runs root, its own ``state.jsonl`` / ``ir.json`` /
``config.snapshot.json`` / evidence. It is linked to its parent (``parent`` / ``call_path`` / ``depth`` /
``depth_limit`` / ``dispatch`` in ``run.json``; the parent keeps ``calls.json``). Its config comes from its own
layers, then is only ever narrowed toward the parent's resolved values (:func:`procflow.config.narrow_for_child`);
the parent's run-level overrides (L6 / L7) and ai target choices never reach it.
"""

from __future__ import annotations

import copy
from pathlib import Path

from agent_policy_kit import SecretMasker

from . import config as cfg
from . import records as R
from . import registry as REG
from . import routing as RT
from .check import Checker
from .compiler import compile_sop
from .errors import ProcflowError

_MASK = SecretMasker()


class CallFail(ProcflowError):
    """The call cannot start (or its result is refused). ``exc`` is the typed exception raised at the call."""

    def __init__(self, exc, note):
        super().__init__(note)
        self.exc, self.note = exc, note


def repo_cfg(repo):
    """What a run records so a later process (``submit`` on a child, ``next`` on a parent) can find definitions."""
    return {"root": str(Path(repo.root).resolve()), "interfaces_dir": str(Path(repo.interfaces_dir).resolve()),
            "config_dir": str(Path(repo.config_dir).resolve())}


def create_child_run(parent, node, target, values, n, how, limit, depth):
    """Create the child run for ``node`` (a call in ``parent``) running procedure ``target`` with ``values``.

    Returns ``(RunDir, narrowing notes)``. Raises :class:`CallFail` (``SystemError``) when it cannot be created; nothing
    is left behind in that case except what ``mkdir`` made after every check passed.
    """
    repo = parent.call_repo()
    if repo is None:
        raise CallFail("SystemError", "a child run needs the procedure root (the run records none; open the run with --sop-root)")
    ch = Checker(repo)
    errs = list(dict.fromkeys(ch.check_sop(target) + ch.check_runnable(target)))
    if errs:
        raise CallFail("SystemError", f"call target {target} does not pass the check: {'; '.join(errs)[:400]}")
    ir, cerrs = compile_sop(target, repo)
    if ir is None:
        raise CallFail("SystemError", f"call target {target} cannot be compiled: {'; '.join(cerrs)[:300]}")
    if node.get("interface") and node["interface"] not in ir.sop.implements:
        raise CallFail("SystemError", f"call target {target} does not implement {node['interface']}")
    at = parent.now()
    try:
        res = cfg.resolve(ir, repo, env=parent.run.get("env"), call_params=values, now=at, strict_inputs=True)
    except cfg.ConfigError as x:
        raise CallFail("SystemError", f"call target {target}: config or inputs are refused: {'; '.join(x.errors)[:400]}") from x
    notes = cfg.narrow_for_child(res["values"], parent.vmeta,
                                 {"calls": parent.st.ai_calls, "cost": parent.st.ai_cost},
                                 node.get("with_config"), node["id"])
    ird = ir.to_dict()
    unrouted = RT.findings(ird, res["values"], parent.reg)
    if unrouted:
        raise CallFail("SystemError", "an llm / agent step of the call target has no execution target: " + "; ".join(unrouted[:3]))
    run_id = R.child_run_id(parent.rd.run_id, node["id"], n)
    if not R.valid_run_id(run_id):
        raise CallFail("SystemError", f"the child run id {run_id!r} is not a valid run id")
    rd = R.RunDir(parent.rd.runs_root, target, run_id)
    if rd.exists():
        raise CallFail("SystemError", f"child run {run_id} already exists")
    inputs = {k: x["value"] for k, x in res["inputs"].items()}
    inputs = {k: (run_id if v == "<decided when the run starts>" else v) for k, v in inputs.items()}
    inputs_sha = R.sha256_json(inputs)
    impl = Path(repo.root) / "_impl" / target
    R.write_json(rd.file("ir.json"), ird)
    REG.pin(rd, parent.reg)
    snap = cfg.mask_result(res)
    doc = {k: snap[k] for k in ("sop", "env", "values", "inputs", "files", "locked", "caps")}
    doc["call"] = {"parent": parent.rd.run_id, "step": node["id"], "n": n, "narrowed": notes,
                   "with_config": copy.deepcopy(node.get("with_config") or {})}
    R.write_json(rd.file("config.snapshot.json"), doc)
    R.write_json(rd.file("inputs.json"), _MASK.mask_obj(inputs))
    R.write_json(rd.file("calls.json"), [])
    prefix = (parent.run.get("step_prefix") or "") + f"{node['id']}-"
    run = {"run_id": run_id, "sop": target, "version": ir.sop.version, "status": "running", "env": res["env"],
           "created": R.iso(at), "ended": None, "outcome": None, "round": 1, "current": [ir.start],
           "inputs_sha256": inputs_sha, "impl_root": str(impl), "skill_roots": [str(x) for x in parent.skill_roots],
           "repo": repo_cfg(repo),
           "version_hashes": {"ir_sha256": R.sha256_json(ird), "impl_sha256": R.tree_sha256(impl),
                              "source_sha256": repo.source_sha256(target)},
           "parent": {"run_id": parent.rd.run_id, "sop": parent.run.get("sop"), "step": node["id"], "n": n},
           "call_path": list(parent.run.get("call_path") or [parent.run.get("sop")]) + [target],
           "depth": depth, "depth_limit": limit, "dispatch": how, "children": [], "trigger": "call",
           "step_prefix": prefix, "raised": None, "suspend": None,
           "history": [{"ts": R.iso(at), "status": "running", "reason": None}]}
    rd.write_run(run)
    ctx0 = {k: copy.deepcopy(v.get("init")) for k, v in (ird.get("context") or {}).items()
            if isinstance(v, dict) and "init" in v}
    rd.append_state({"ev": "start", "tok": "t1", "node": ir.start, "inputs": _MASK.mask_obj(inputs), "ctx": ctx0,
                     "sop": target, "version": ir.sop.version, "parent": parent.rd.run_id}, ts=R.iso(at))
    if notes:
        shown = ", ".join(f"{x['key']}->{x.get('to')}" + (f" ({x['ignored']})" if x.get("ignored") else "") for x in notes)
        rd.append_state({"ev": "note", "note": _MASK.mask_text(f"call.narrowed by {parent.rd.run_id}/{node['id']}: {shown}")[:600]},
                        ts=R.iso(at))
    R.append_index(parent.rd.runs_root, run)
    return rd, notes
