"""Optional TOML/dict config that assembles a scope policy, masker and audit."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .audit import AuditLog
from .scope import ScopePolicy
from .secrets import SecretMasker

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class KitConfig:
    scope: ScopePolicy
    masker: SecretMasker
    audit: AuditLog

    @staticmethod
    def from_dict(d: dict) -> "KitConfig":
        d = d or {}
        sc = d.get("scope", {}) or {}
        resources = {k: set(v) for k, v in (sc.get("resources") or {}).items()}
        scope = ScopePolicy(
            resources,
            read_only=bool(sc.get("read_only", True)),
            allow_unlisted=bool(sc.get("allow_unlisted", False)),
        )
        se = d.get("secret_filter", {}) or {}
        masker = SecretMasker(
            builtin=bool(se.get("builtin", True)),
            extra_patterns=list(se.get("extra_patterns", [])),
        )
        audit = AuditLog((d.get("audit") or {}).get("path"))
        return KitConfig(scope=scope, masker=masker, audit=audit)

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "KitConfig":
        path = path or os.environ.get("AGENT_POLICY_KIT_CONFIG")
        if not path:
            return KitConfig.from_dict({})
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            return KitConfig.from_dict(tomllib.load(fh))
