"""Configuration model and loader (TOML). Sources are an allowlist."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .errors import SourceNotAllowed

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

DEFAULT_PERIODS = [60, 300, 900, 3600, 21600, 86400]


@dataclass
class SourceConfig:
    name: str
    type: str
    options: dict = field(default_factory=dict)


@dataclass
class OutputConfig:
    dir: str = "metrics-out"
    max_points: int = 1440


@dataclass
class AuditConfig:
    path: str | None = None


@dataclass
class Config:
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    output: OutputConfig = field(default_factory=OutputConfig)
    candidate_periods: list[int] = field(default_factory=lambda: list(DEFAULT_PERIODS))
    audit: AuditConfig = field(default_factory=AuditConfig)

    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = d or {}
        cfg = Config()
        if "output" in d:
            o = d["output"]
            cfg.output = OutputConfig(dir=o.get("dir", "metrics-out"),
                                      max_points=int(o.get("max_points", 1440)))
        if "candidate_periods" in d:
            cfg.candidate_periods = sorted(int(x) for x in d["candidate_periods"])
        if "audit" in d:
            cfg.audit = AuditConfig(path=d["audit"].get("path"))
        for name, spec in (d.get("sources") or {}).items():
            if isinstance(spec, dict) and "type" in spec:
                opts = {k: v for k, v in spec.items() if k != "type"}
                cfg.sources[name] = SourceConfig(name=name, type=spec["type"], options=opts)
        cfg.validate()
        return cfg

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "Config":
        path = path or os.environ.get("MFETCH_CONFIG")
        if not path and os.path.isfile("mfetch.toml"):
            path = "mfetch.toml"
        if not path:
            cfg = Config()
            cfg.validate()
            return cfg
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            return Config.from_dict(tomllib.load(fh))

    def validate(self) -> None:
        if self.output.max_points < 1:
            raise ValueError("output.max_points must be >= 1")
        if not self.candidate_periods:
            raise ValueError("candidate_periods must not be empty")

    def get_source(self, name: str) -> SourceConfig:
        if name not in self.sources:
            raise SourceNotAllowed(f"source {name!r} is not configured (fail-closed)")
        return self.sources[name]
