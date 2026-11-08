"""Configuration model and loader (TOML). Targets are an allowlist."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .errors import TargetNotAllowed

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class Limits:
    max_scanned_rows: int = 1_000_000
    timeout_seconds: float = 30.0
    max_rows: int = 1000


@dataclass
class Target:
    name: str
    driver: str
    dsn: str


@dataclass
class AuditConfig:
    path: str | None = None


@dataclass
class WriteConfig:
    enabled: bool = False                       # opt-in; read-only by default
    allow: set[str] = field(default_factory=set)   # allowed leading keywords
    require_approval: bool = True               # preview -> human approval


@dataclass
class Config:
    targets: dict[str, Target] = field(default_factory=dict)
    limits: Limits = field(default_factory=Limits)
    audit: AuditConfig = field(default_factory=AuditConfig)
    write: WriteConfig = field(default_factory=WriteConfig)

    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = d or {}
        cfg = Config()
        if "limits" in d:
            lm = d["limits"]
            cfg.limits = Limits(
                max_scanned_rows=int(lm.get("max_scanned_rows", 1_000_000)),
                timeout_seconds=float(lm.get("timeout_seconds", 30.0)),
                max_rows=int(lm.get("max_rows", 1000)),
            )
        if "audit" in d:
            cfg.audit = AuditConfig(path=d["audit"].get("path"))
        if "write" in d:
            w = d["write"]
            cfg.write = WriteConfig(
                enabled=bool(w.get("enabled", False)),
                allow={str(k).lower() for k in w.get("allow", [])},
                require_approval=bool(w.get("require_approval", True)),
            )
        for name, spec in (d.get("targets") or {}).items():
            if isinstance(spec, dict) and "driver" in spec and "dsn" in spec:
                cfg.targets[name] = Target(name=name, driver=spec["driver"], dsn=spec["dsn"])
        cfg.validate()
        return cfg

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "Config":
        path = path or os.environ.get("SELECTGATE_CONFIG")
        if not path and os.path.isfile("selectgate.toml"):
            path = "selectgate.toml"
        if not path:
            cfg = Config()
            cfg.validate()
            return cfg
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            return Config.from_dict(tomllib.load(fh))

    def validate(self) -> None:
        if self.limits.max_scanned_rows < 0:
            raise ValueError("limits.max_scanned_rows must be >= 0")
        if self.limits.timeout_seconds <= 0:
            raise ValueError("limits.timeout_seconds must be > 0")
        if self.limits.max_rows < 1:
            raise ValueError("limits.max_rows must be >= 1")

    def get_target(self, name: str) -> Target:
        if name not in self.targets:
            raise TargetNotAllowed(
                f"target {name!r} is not configured (add a [targets.{name}] entry); refusing (fail-closed)"
            )
        return self.targets[name]
