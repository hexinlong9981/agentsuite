"""Typed views over a definition (authoring model, before compilation).

These are thin, read-only views: the YAML dict stays the record of truth and the
compiler works on it. ``from_dict`` is lenient — shape errors are reported by
``defschema.check_definition``, not raised here.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


def as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


@dataclass
class Step:
    id: str
    type: str
    name: str | None = None
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Step":
        return cls(id=str(d.get("id")), type=str(d.get("type")), name=d.get("name"), raw=copy.deepcopy(d))

    def get(self, key, default=None):
        return self.raw.get(key, default)


@dataclass
class Transition:
    id: str | None
    src: str | None
    dst: str | None
    guard: str | None = None
    label: Any = None
    default: bool = False
    final: bool = False
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Transition":
        return cls(id=d.get("id"), src=d.get("from"), dst=d.get("to"), guard=d.get("guard"),
                   label=d.get("when"), default=bool(d.get("default")), final=bool(d.get("final")),
                   raw=copy.deepcopy(d))


@dataclass
class InterfaceSpec:
    """An interface contract (``IF-*.yaml``) or a procedure's own interface block."""

    id: str | None = None
    version: str | None = None
    name: str | None = None
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    raises: list = field(default_factory=list)
    pre: list = field(default_factory=list)
    post: list = field(default_factory=list)
    safety: dict = field(default_factory=dict)
    default_impl: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "InterfaceSpec":
        d = d or {}
        return cls(id=d.get("id"), version=None if d.get("version") is None else str(d.get("version")),
                   name=d.get("name"), inputs=copy.deepcopy(d.get("inputs") or {}),
                   outputs=copy.deepcopy(d.get("outputs") or {}), raises=list(d.get("raises") or []),
                   pre=list(d.get("pre") or []), post=list(d.get("post") or []),
                   safety=copy.deepcopy(d.get("safety") or {}), default_impl=d.get("default_impl"))

    @property
    def major(self) -> str:
        return str(self.version or "").split(".")[0]

    def to_dict(self) -> dict:
        return {"id": self.id, "version": self.version, "name": self.name, "inputs": self.inputs,
                "outputs": self.outputs, "raises": self.raises, "pre": self.pre, "post": self.post,
                "safety": self.safety, "default_impl": self.default_impl}


@dataclass
class Definition:
    id: str | None
    name: str | None
    version: str
    extends: str | None = None
    abstract: bool = False
    implements: list = field(default_factory=list)
    human_only: list = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    interface: InterfaceSpec | None = None
    context: dict = field(default_factory=dict)
    runtime: dict | None = None
    finally_: list = field(default_factory=list)
    flow: dict = field(default_factory=dict)
    source: str | None = None
    sha256: str | None = None

    @classmethod
    def from_dict(cls, d: dict, source: str | None = None, sha256: str | None = None) -> "Definition":
        sop = d.get("sop") if isinstance(d.get("sop"), dict) else {}
        itf = d.get("interface")
        return cls(
            id=sop.get("id"), name=sop.get("name"), version=str(sop.get("version")),
            extends=sop.get("extends") if isinstance(sop.get("extends"), str) else None,
            abstract=sop.get("abstract") is True, implements=as_list(sop.get("implements")),
            human_only=as_list(sop.get("human_only")),
            steps=[Step.from_dict(s) for s in d.get("steps") or [] if isinstance(s, dict)],
            transitions=[Transition.from_dict(t) for t in d.get("transitions") or [] if isinstance(t, dict)],
            interface=InterfaceSpec.from_dict(itf) if isinstance(itf, dict) else None,
            context=copy.deepcopy(d.get("context") or {}) if isinstance(d.get("context"), dict) else {},
            runtime=copy.deepcopy(d.get("runtime")) if isinstance(d.get("runtime"), dict) else None,
            finally_=as_list(d.get("finally")), flow=copy.deepcopy(d.get("flow") or {}),
            source=source, sha256=sha256)
