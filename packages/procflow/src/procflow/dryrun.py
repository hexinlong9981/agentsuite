"""dry-run: enumerate the reachable paths of a procedure without creating a run.

Guards and labels are not evaluated; every candidate branch is followed. A loop is shown once (its body
listed), parallel branches are shown together as ``{a || b}``, a back-edge that raises the round is
followed up to the round ceiling, and a ``call`` ends the path with a note. Nothing is launched
and nothing is written.
"""

from __future__ import annotations

import itertools
import re

MAX_PATHS = 200


def _round_up(t):
    return any("round" in str(x) for x in (t.get("on_take") or []))


class Enumerator:
    def __init__(self, ird, max_rounds=3, max_paths=MAX_PATHS):
        self.ir = ird
        self.nodes = {n["id"]: n for n in ird["nodes"]}
        self.outs = {}
        for t in ird["transitions"]:
            self.outs.setdefault(t["from"], []).append(t)
        ex = ird.get("execution") or {}
        mr = ex.get("max_rounds")
        self.max_rounds = min(max_rounds, mr) if isinstance(mr, int) and not isinstance(mr, bool) else max_rounds
        self.max_paths = max_paths
        self.truncated = False

    def tag(self, n):
        k = n.get("kind") if n["type"] == "task" else n["type"]
        extra = ""
        if n["type"] == "loop":
            extra = " each " + str(n.get("foreach") or "while") + " [" + ", ".join(n.get("body") or []) + "]"
        return f"{n['id']}({k}{extra})"

    def walk(self, nid, rounds, seen, branch):
        """-> list of (trail, terminal) ; terminal = ("end", outcome) | ("join", id) | ("suspend", why) | ("cut", why)"""
        n = self.nodes.get(nid)
        if n is None:
            return [([nid], ("cut", "unknown node"))]
        if seen.get(nid, 0) >= 2:
            return [([self.tag(n)], ("cut", "revisit"))]
        seen = {**seen, nid: seen.get(nid, 0) + 1}
        typ = n["type"]
        here = [self.tag(n)]
        if typ == "end":
            return [(here, ("end", n.get("outcome") or "success"))]
        if typ == "join" and branch:
            return [([], ("join", nid))]
        outs = list(self.outs.get(nid, []))
        if typ == "call":     # a call continues like a task; each catch is one more way out
            outs += [{"id": None, "from": nid, "to": tgt, "label": f"catch {exc}"}
                     for exc, tgt in (n.get("catch") or {}).items() if tgt in self.nodes]
        if not outs:
            return [(here, ("cut", "no outgoing transition"))]
        results = []
        if typ == "parallel_split":
            subs = [self.walk(t["to"], rounds, seen, True) for t in outs]
            for combo in itertools.islice(itertools.product(*subs), self.max_paths):
                joins = {c[1][1] for c in combo if c[1][0] == "join"}
                block = "{" + " || ".join(" > ".join(c[0]) or "-" for c in combo) + "}"
                if len(joins) == 1 and all(c[1][0] == "join" for c in combo):
                    jn = self.nodes[next(iter(joins))]
                    for tr, term in self.walk_after(jn, rounds, seen, branch):
                        results.append((here + [block] + tr, term))
                else:
                    term = next((c[1] for c in combo if c[1][0] != "join"), ("cut", "unmatched join"))
                    results.append((here + [block], term))
            return results
        for t in outs:
            up = _round_up(t)
            if up and rounds + 1 > self.max_rounds:
                continue
            label = t.get("label") if t.get("label") is not None else (t.get("guard") or ("default" if t.get("default") else None))
            edge = [f"-[{label}]->"] if len(outs) > 1 and label is not None else []
            for tr, term in self.walk(t["to"], rounds + (1 if up else 0), seen, branch):
                results.append((here + edge + tr, term))
            if len(results) >= self.max_paths:
                self.truncated = True
                break
        return results

    def walk_after(self, jn, rounds, seen, branch):
        """Continue from a join node (it is part of the path, then its successors)."""
        res = []
        for t in self.outs.get(jn["id"], []):
            for tr, term in self.walk(t["to"], rounds, seen, branch):
                res.append(([self.tag(jn)] + tr, term))
        return res or [([self.tag(jn)], ("cut", "no outgoing transition"))]


def enumerate_paths(ird, max_rounds=3, max_paths=MAX_PATHS):
    en = Enumerator(ird, max_rounds, max_paths)
    raw = en.walk(ird["start"], 1, {}, False)
    paths = []
    for trail, term in raw[:max_paths]:
        p_text = " ".join(trail)
        paths.append({"path": " > ".join(trail), "terminal": term[0], "detail": term[1],
                      "human_steps": re.findall(r"([A-Za-z0-9_\-]+)\(human", p_text),
                      "ends_with": term[1] if term[0] == "end" else None})
    return {"sop": ird["sop"]["id"], "paths": paths, "count": len(paths),
            "truncated": en.truncated or len(raw) > max_paths, "finally": ird.get("finally") or [],
            "call_nodes": [n["id"] for n in ird["nodes"] if n["type"] == "call"]}
