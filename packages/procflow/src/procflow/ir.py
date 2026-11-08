"""The intermediate representation (IR) a definition compiles to.

A run executes off an IR snapshot taken at start; later edits to the definition do
not affect it. ``to_dict()`` is the JSON-serialisable form (``procflow compile``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

IR_VERSION = 1


@dataclass
class IRSop:
    id: str | None
    name: str | None
    version: str
    abstract: bool                      # declared abstract OR abstract steps remain
    declared_abstract: bool = False
    abstract_steps: list = field(default_factory=list)
    extends: str | None = None
    implements: list = field(default_factory=list)
    human_only: list = field(default_factory=list)
    source: str | None = None
    sha256: str | None = None


@dataclass
class IRInterface:
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    raises: list = field(default_factory=list)
    origin: str = "declared"            # declared | derived
    raises_origin: str | None = None    # "derived" when raises were collected from steps


@dataclass
class IRNode:
    id: str
    type: str
    origin: str | None = None           # the procedure that defined this step
    kind: str | None = None             # task only: code | llm | agent | human
    visibility: str | None = None
    final: bool = False
    abstract: bool = False
    hook: bool = False
    raises: list = field(default_factory=list)
    override: str | None = None
    super_of: str | None = None
    human_review: bool = False
    title: str | None = None
    props: dict = field(default_factory=dict)       # type-specific copied keys (body, with, dispatch, ...)
    definition: dict = field(default_factory=dict)  # merged step definition in full ("def")
    interface_spec: dict | None = None              # inlined contract for interface calls

    def get(self, key, default=None):
        return self.props.get(key, default)

    def to_dict(self) -> dict:
        out = {"id": self.id, "type": self.type, "origin": self.origin}
        if self.override:
            out["override"] = self.override
        if self.super_of:
            out["super_of"] = self.super_of
        out.update(self.props)
        if self.type == "task":
            out.update(kind=self.kind, visibility=self.visibility, abstract=self.abstract, hook=self.hook,
                       raises=self.raises, title=self.title)
            if self.human_review:
                out["human_review"] = True
        out["final"] = self.final
        out["def"] = self.definition
        if self.interface_spec is not None:
            out["interface_spec"] = self.interface_spec
        return out


@dataclass
class IRTransition:
    id: str | None
    src: str | None
    dst: str | None
    label: object = None
    guard: str | None = None
    default: bool = False
    on_take: object = None
    data: object = None
    final: bool = False

    def to_dict(self) -> dict:
        out = {"id": self.id, "from": self.src, "to": self.dst}
        for k in ("label", "guard", "on_take", "data"):
            if getattr(self, k) is not None:
                out[k] = getattr(self, k)
        if self.default:
            out["default"] = True
        if self.final:
            out["final"] = True
        return out


@dataclass
class IRCall:
    step: str
    sop: str | None = None
    interface: str | None = None
    dispatch: list = field(default_factory=list)


@dataclass
class IR:
    sop: IRSop
    interface: IRInterface
    context: dict = field(default_factory=dict)
    runtime_layers: list = field(default_factory=list)   # [[origin-id, runtime-dict], ...] base -> self
    start: str | None = None
    execution: dict = field(default_factory=dict)
    finally_: list = field(default_factory=list)
    nodes: list[IRNode] = field(default_factory=list)
    transitions: list[IRTransition] = field(default_factory=list)
    calls: list[IRCall] = field(default_factory=list)
    merge: dict = field(default_factory=dict)            # parent_id / replaced / overrides
    raw_flow: dict = field(default_factory=dict)
    builtin_exceptions: list = field(default_factory=list)

    def node(self, node_id):
        return next((n for n in self.nodes if n.id == node_id), None)

    def to_dict(self) -> dict:
        s = self.sop
        return {
            "ir_version": IR_VERSION,
            "sop": {"id": s.id, "name": s.name, "version": s.version, "declared_abstract": s.declared_abstract,
                    "abstract": s.abstract, "abstract_steps": s.abstract_steps, "extends": s.extends,
                    "implements": s.implements, "human_only": s.human_only, "source": s.source,
                    "sha256": s.sha256},
            "interface": {"inputs": self.interface.inputs, "outputs": self.interface.outputs,
                          "raises": self.interface.raises, "origin": self.interface.origin,
                          **({"raises_origin": self.interface.raises_origin} if self.interface.raises_origin else {})},
            "context": self.context,
            "runtime_layers": self.runtime_layers,
            "start": self.start,
            "execution": self.execution,
            "finally": self.finally_,
            "nodes": [n.to_dict() for n in self.nodes],
            "transitions": [t.to_dict() for t in self.transitions],
            "calls": [{"step": c.step, "sop": c.sop, "interface": c.interface, "dispatch": c.dispatch}
                      for c in self.calls],
            "builtin_exceptions": self.builtin_exceptions,
            "merge": self.merge,
        }
