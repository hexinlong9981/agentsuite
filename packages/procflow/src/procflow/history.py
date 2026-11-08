"""runs / show / log / steps-json over ``state.jsonl`` and the index. Read-only: no lock is taken."""

from __future__ import annotations

from . import lock as L
from . import records as R

ENDED = ("done", "failed", "cancelled")


def runs(runs_root, status=None, sop=None):
    rows = R.read_index(runs_root) or [R.index_entry(r) for r in R.scan_runs(runs_root)]
    rows = [r for r in rows if (not status or r.get("status") == status) and (not sop or r.get("sop") == sop)]
    return sorted(rows, key=lambda r: r.get("created") or "")


def show(rd):
    run = rd.read_run()
    evs = rd.read_state()
    steps = steps_json(rd, evs)
    pend = [e for e in evs if e["ev"] == "ticket"]
    closed = {e["ticket"] for e in evs if e["ev"] in ("ticket_done", "ticket_discard")}
    return {"run": run, "events": len(evs), "last_seq": evs[-1]["seq"] if evs else 0,
            "steps": {"count": len(steps), "ok": sum(1 for s in steps if s["result"] == "ok"),
                      "error": sum(1 for s in steps if s["result"] == "error")},
            "pending_tickets": [e["ticket"] for e in pend if e["ticket"] not in closed],
            "lock": L.holder(rd.file(".lock")), "location": str(rd.path)}


def _line(e):
    ev = e["ev"]
    bits = [str(e.get(k)) for k in ("step", "node", "loop", "from", "to", "reason", "status", "decision", "result", "label",
                                    "child", "type", "raised")
            if e.get(k) is not None]
    if ev == "ticket":
        bits = [e.get("type", ""), e.get("ticket", "")]
    return f"{e['seq']:>4} {e.get('ts', '')} {ev:<14} " + " ".join(bits)


def log(rd, tail=None):
    lines = [_line(e) for e in rd.read_state()]
    return lines[-tail:] if tail else lines


def steps_json(rd, evs=None):
    """One row per executed step attempt: result and evidence locations / counts (never data)."""
    evs = evs if evs is not None else rd.read_state()
    rows = []
    for e in evs:
        if e["ev"] not in ("step_end", "call_end"):
            continue
        ev = e.get("evidence") or []
        rows.append({"step": e["step"], "round": e.get("round", 1), "attempt": e.get("attempt"), "result": e["result"],
                     "ts": e.get("ts"), "decision": e.get("decision"), "finally": bool(e.get("finally")), "child": e.get("child"),
                     "noop": bool(e.get("noop")), "evidence_dir": f"{e.get('round', 1)}/{R.safe_dir(e['step'])}",
                     "evidence_files": len(ev), "evidence_bytes": sum(x.get("bytes", 0) for x in ev if isinstance(x, dict)),
                     "output_names": sorted((e.get("outputs") or {}).keys())})
    return rows


def tree_lines(tree, _indent=""):
    """The call tree as text lines (``procflow show --tree``)."""
    n = tree
    head = f"{n['run_id']}  {n.get('sop')}  {n.get('status')}"
    if n.get("step"):
        head += f"  (call {n['step']}, depth {n.get('depth')})"
    if n.get("raised"):
        head += f"  raised {n['raised']}"
    lines = [_indent + head]
    kids = n.get("children") or []
    for i, c in enumerate(kids):
        last = i == len(kids) - 1
        sub = tree_lines(c)
        lines.append(_indent + ("`- " if last else "|- ") + sub[0])
        lines += [_indent + ("   " if last else "|  ") + x for x in sub[1:]]
    return lines
