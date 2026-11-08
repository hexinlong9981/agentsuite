"""Configuration: the folder allowlist, backend selection, audit (TOML/dict).

No credentials live here. A real backend reads its credentials from the
environment / a secret store at connect time (never from this config or the repo).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from agent_policy_kit import AuditLog, ScopePolicy, SecretMasker

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class Config:
    backend_type: str = "fake"
    backend_options: dict = field(default_factory=dict)
    scope: ScopePolicy = field(default_factory=lambda: ScopePolicy({}, read_only=True))
    audit: AuditLog = field(default_factory=lambda: AuditLog(None))
    masker: SecretMasker = field(default_factory=SecretMasker)

    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = d or {}
        folders = {name: set(ops) for name, ops in (d.get("folders") or {}).items()}
        read_only = bool(d.get("read_only", True))
        scope = ScopePolicy(folders, read_only=read_only)
        backend = dict(d.get("backend") or {})
        backend_type = backend.pop("type", "fake")
        audit = AuditLog((d.get("audit") or {}).get("path"))
        return Config(backend_type=backend_type, backend_options=backend,
                      scope=scope, audit=audit, masker=SecretMasker())

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "Config":
        path = path or os.environ.get("FILEPEEK_CONFIG")
        if not path and os.path.isfile("filepeek.toml"):
            path = "filepeek.toml"
        if not path:
            return Config.from_dict({})
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            return Config.from_dict(tomllib.load(fh))
