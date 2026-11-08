"""Configuration model and loader (TOML). Targets and policies are allowlists."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .errors import TargetNotAllowed

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class Rule:
    program: str
    args_regex: str | None = None


@dataclass
class Policy:
    name: str
    targets: list[str] = field(default_factory=list)
    read_only: bool = True
    allow_shell: bool = False
    allow: list[Rule] = field(default_factory=list)


@dataclass
class Target:
    name: str
    kind: str = "local"            # local | ssh
    host: str | None = None
    user: str | None = None
    port: int | None = None
    jump: list[str] = field(default_factory=list)   # bastion / jump-host chain (ProxyJump)
    ssh_options: list[str] = field(default_factory=list)


@dataclass
class Limits:
    timeout_seconds: float = 60.0
    max_output_bytes: int = 1_000_000


@dataclass
class AuditConfig:
    path: str | None = None


@dataclass
class Config:
    targets: dict[str, Target] = field(default_factory=dict)
    policies: dict[str, Policy] = field(default_factory=dict)
    limits: Limits = field(default_factory=Limits)
    audit: AuditConfig = field(default_factory=AuditConfig)

    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = d or {}
        cfg = Config()
        if "limits" in d:
            lm = d["limits"]
            cfg.limits = Limits(
                timeout_seconds=float(lm.get("timeout_seconds", 60.0)),
                max_output_bytes=int(lm.get("max_output_bytes", 1_000_000)),
            )
        if "audit" in d:
            cfg.audit = AuditConfig(path=d["audit"].get("path"))
        for name, spec in (d.get("targets") or {}).items():
            if isinstance(spec, dict):
                cfg.targets[name] = Target(
                    name=name, kind=spec.get("kind", "local"),
                    host=spec.get("host"), user=spec.get("user"),
                    port=spec.get("port"),
                    jump=list(spec.get("jump", [])),
                    ssh_options=list(spec.get("ssh_options", [])),
                )
        for name, spec in (d.get("policies") or {}).items():
            if not isinstance(spec, dict):
                continue
            rules = [Rule(program=r["program"], args_regex=r.get("args_regex"))
                     for r in spec.get("allow", []) if isinstance(r, dict) and "program" in r]
            cfg.policies[name] = Policy(
                name=name, targets=list(spec.get("targets", [])),
                read_only=bool(spec.get("read_only", True)),
                allow_shell=bool(spec.get("allow_shell", False)),
                allow=rules,
            )
        cfg.validate()
        return cfg

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "Config":
        path = path or os.environ.get("CMDGATE_CONFIG")
        if not path and os.path.isfile("cmdgate.toml"):
            path = "cmdgate.toml"
        if not path:
            cfg = Config()
            cfg.validate()
            return cfg
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            return Config.from_dict(tomllib.load(fh))

    def validate(self) -> None:
        if self.limits.timeout_seconds <= 0:
            raise ValueError("limits.timeout_seconds must be > 0")
        for pol in self.policies.values():
            for t in pol.targets:
                if t not in self.targets:
                    raise ValueError(f"policy {pol.name!r} references unknown target {t!r}")

    def get_target(self, name: str) -> Target:
        if name not in self.targets:
            raise TargetNotAllowed(
                f"target {name!r} is not configured (add a [targets.{name}] entry); refusing (fail-closed)"
            )
        return self.targets[name]

    def policies_for_target(self, target: str) -> list[Policy]:
        return [p for p in self.policies.values() if target in p.targets]
