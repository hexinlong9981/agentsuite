"""Configuration model and loader (TOML). Sources and policies are allowlists."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .errors import DestinationError, PolicyNotAllowed, SourceNotAllowed

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class Source:
    name: str
    transport: str = "local"          # local | ssh
    root: str | None = None
    host: str | None = None
    user: str | None = None
    port: int | None = None
    jump: list[str] = field(default_factory=list)
    path: str | None = None
    ssh_options: list[str] = field(default_factory=list)


@dataclass
class ClassifyRule:
    pattern: str
    category: str


@dataclass
class Policy:
    name: str
    source: str
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    classify: list[ClassifyRule] = field(default_factory=list)
    default_category: str = "data"


@dataclass
class Limits:
    max_files: int = 10000
    max_bytes: int = 1_000_000_000
    max_file_bytes: int = 0           # 0 = unlimited


@dataclass
class AuditConfig:
    path: str | None = None


@dataclass
class Config:
    sources: dict[str, Source] = field(default_factory=dict)
    destinations: dict[str, str] = field(default_factory=dict)
    policies: dict[str, Policy] = field(default_factory=dict)
    limits: Limits = field(default_factory=Limits)
    audit: AuditConfig = field(default_factory=AuditConfig)

    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = d or {}
        cfg = Config()
        cfg.destinations = dict(d.get("destinations") or {})
        if "limits" in d:
            lm = d["limits"]
            cfg.limits = Limits(
                max_files=int(lm.get("max_files", 10000)),
                max_bytes=int(lm.get("max_bytes", 1_000_000_000)),
                max_file_bytes=int(lm.get("max_file_bytes", 0)),
            )
        if "audit" in d:
            cfg.audit = AuditConfig(path=d["audit"].get("path"))
        for name, spec in (d.get("sources") or {}).items():
            if isinstance(spec, dict):
                cfg.sources[name] = Source(
                    name=name, transport=spec.get("transport", "local"),
                    root=spec.get("root"), host=spec.get("host"),
                    user=spec.get("user"), port=spec.get("port"),
                    jump=list(spec.get("jump", []) if isinstance(spec.get("jump"), list) else [spec.get("jump")] if spec.get("jump") else []),
                    path=spec.get("path"),
                    ssh_options=list(spec.get("ssh_options", [])),
                )
        for name, spec in (d.get("policies") or {}).items():
            if not isinstance(spec, dict) or "source" not in spec:
                continue
            rules = [ClassifyRule(pattern=r["pattern"], category=r["category"])
                     for r in spec.get("classify", [])
                     if isinstance(r, dict) and "pattern" in r and "category" in r]
            cfg.policies[name] = Policy(
                name=name, source=spec["source"],
                include=list(spec.get("include", [])),
                exclude=list(spec.get("exclude", [])),
                classify=rules,
                default_category=spec.get("default_category", "data"),
            )
        cfg.validate()
        return cfg

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "Config":
        path = path or os.environ.get("RFSYNC_CONFIG")
        if not path and os.path.isfile("rfsync.toml"):
            path = "rfsync.toml"
        if not path:
            cfg = Config()
            cfg.validate()
            return cfg
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            return Config.from_dict(tomllib.load(fh))

    def validate(self) -> None:
        for pol in self.policies.values():
            if pol.source not in self.sources:
                raise ValueError(f"policy {pol.name!r} references unknown source {pol.source!r}")
            cats = {pol.default_category} | {r.category for r in pol.classify}
            for cat in cats:
                if cat not in self.destinations:
                    raise ValueError(
                        f"policy {pol.name!r} uses category {cat!r} with no destination")

    def get_policy(self, name: str) -> Policy:
        if name not in self.policies:
            raise PolicyNotAllowed(f"policy {name!r} is not configured (fail-closed)")
        return self.policies[name]

    def get_source(self, name: str) -> Source:
        if name not in self.sources:
            raise SourceNotAllowed(f"source {name!r} is not configured (fail-closed)")
        return self.sources[name]

    def dest_root(self, category: str) -> str:
        if category not in self.destinations:
            raise DestinationError(f"no destination configured for category {category!r}")
        return self.destinations[category]
