"""Configuration: the host matrix and audit path (from TOML/dict)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .audit import AuditLog
from .policy import CATEGORIES, HostPolicy

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class Config:
    policy: HostPolicy
    audit: AuditLog

    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = d or {}
        hosts: dict[str, set[str]] = {}
        for name, spec in (d.get("hosts") or {}).items():
            if isinstance(spec, dict):
                ops = set(spec.get("allow", []))
                bad = ops - CATEGORIES
                if bad:
                    raise ValueError(f"host {name!r} has unknown categories: {sorted(bad)}")
                hosts[name] = ops
        audit = AuditLog((d.get("audit") or {}).get("path"))
        return Config(policy=HostPolicy(hosts), audit=audit)

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "Config":
        path = path or os.environ.get("WEB_OPERATOR_CONFIG")
        if not path and os.path.isfile("web_operator.toml"):
            path = "web_operator.toml"
        if not path:
            return Config.from_dict({})
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            return Config.from_dict(tomllib.load(fh))
