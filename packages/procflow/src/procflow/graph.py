"""IR -> diagram text: mermaid (default) or Graphviz dot."""

from __future__ import annotations

SHAPE_MERMAID = {
    "start": ("([", "])"), "end": ("([", "])"), "task": ("[", "]"),
    "exclusive_choice": ("{", "}"), "inclusive_choice": ("{", "}"),
    "parallel_split": ("[[", "]]"), "join": ("[[", "]]"), "loop": ("[/", "/]"), "call": ("[[", "]]"),
}


def _nid(x):
    return "n_" + "".join(c if c.isalnum() else "_" for c in str(x))


def _label(n):
    if n.type == "task":
        return f"{n.id} ({n.kind or '?'})" + (" abstract" if n.abstract else "") + (" final" if n.final else "")
    if n.type == "call":
        return f"{n.id} call {n.get('sop') or n.get('interface')}"
    if n.type == "loop":
        return f"{n.id} loop <= {n.get('max_iterations')}"
    if n.type == "end":
        return f"{n.id} ({n.get('outcome')})"
    return n.id


def _esc(s):
    return str(s).replace('"', "'")


def _edge_label(t):
    return t.guard or (str(t.label) if t.label is not None else "") or ("default" if t.default else "")


def mermaid(ir):
    s = ir.sop
    head = f"  %% {s.id} v{s.version}"
    if s.extends:
        head += f" extends {s.extends}"
    if s.implements:
        head += f" implements {', '.join(s.implements)}"
    lines = ["flowchart TD", head]
    for n in ir.nodes:
        a, b = SHAPE_MERMAID.get(n.type, ("[", "]"))
        lines.append(f'  {_nid(n.id)}{a}"{_esc(_label(n))}"{b}')
    for t in ir.transitions:
        lab = _edge_label(t)
        arrow = f'-->|"{_esc(lab)}"|' if lab else "-->"
        lines.append(f"  {_nid(t.src)} {arrow} {_nid(t.dst)}")
    for n in ir.nodes:
        if n.type == "loop":
            for b in n.get("body") or []:
                lines.append(f'  {_nid(n.id)} -.->|"body"| {_nid(b)}')
    return "\n".join(lines) + "\n"


def dot(ir):
    lines = [f'digraph "{ir.sop.id}" {{', "  rankdir=TB;"]
    shape = {"start": "oval", "end": "oval", "exclusive_choice": "diamond", "inclusive_choice": "diamond",
             "loop": "parallelogram", "call": "box3d"}
    for n in ir.nodes:
        lines.append(f'  {_nid(n.id)} [label="{_esc(_label(n))}" shape={shape.get(n.type, "box")}];')
    for t in ir.transitions:
        lines.append(f'  {_nid(t.src)} -> {_nid(t.dst)} [label="{_esc(_edge_label(t))}"];')
    for n in ir.nodes:
        if n.type == "loop":
            for b in n.get("body") or []:
                lines.append(f'  {_nid(n.id)} -> {_nid(b)} [style=dashed label="body"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def render(ir, fmt="mermaid"):
    if fmt == "mermaid":
        return mermaid(ir)
    if fmt == "dot":
        return dot(ir)
    raise ValueError(f"unknown graph format {fmt!r} (mermaid | dot)")
