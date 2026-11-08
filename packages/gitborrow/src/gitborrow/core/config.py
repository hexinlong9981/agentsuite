"""Configuration model and loader.

Search order (first hit wins), so "saved but not applied" cannot happen:
  1. explicit path argument / env GITBORROW_CONFIG
  2. ./gitborrow.toml
  3. built-in defaults
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

_DEFAULT_DIRNAME = "gitborrow"


def _default_ephemeral_dir() -> str:
    for base in ("/dev/shm", tempfile.gettempdir()):
        if os.path.isdir(base):
            return str(Path(base) / _DEFAULT_DIRNAME)
    return str(Path(tempfile.gettempdir()) / _DEFAULT_DIRNAME)


@dataclass
class EphemeralConfig:
    dir: str = field(default_factory=_default_ephemeral_dir)
    ttl_hours: float = 12.0


@dataclass
class ContainmentConfig:
    protected_roots: list[str] = field(default_factory=list)


@dataclass
class PushGuardConfig:
    enabled: bool = True


@dataclass
class SecretFilterConfig:
    enabled: bool = True
    action: str = "mask"          # mask | delete | fail
    builtin_rules: bool = True
    extra_rules: list[str] = field(default_factory=list)
    max_file_bytes: int = 2_000_000


@dataclass
class CredentialsConfig:
    source: str = "env"           # env | keyring | file


@dataclass
class RepoEntry:
    url: str
    allowed_refs: list[str] = field(default_factory=list)


@dataclass
class RepositoriesConfig:
    allow_any: bool = False
    entries: dict[str, RepoEntry] = field(default_factory=dict)


@dataclass
class AuditConfig:
    path: str | None = None


@dataclass
class Config:
    ephemeral: EphemeralConfig = field(default_factory=EphemeralConfig)
    containment: ContainmentConfig = field(default_factory=ContainmentConfig)
    push_guard: PushGuardConfig = field(default_factory=PushGuardConfig)
    secret_filter: SecretFilterConfig = field(default_factory=SecretFilterConfig)
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    repositories: RepositoriesConfig = field(default_factory=RepositoriesConfig)
    audit_log: AuditConfig = field(default_factory=AuditConfig)

    # -- construction ------------------------------------------------------
    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = d or {}
        cfg = Config()
        if "ephemeral" in d:
            e = d["ephemeral"]
            cfg.ephemeral = EphemeralConfig(
                dir=e.get("dir", cfg.ephemeral.dir),
                ttl_hours=float(e.get("ttl_hours", cfg.ephemeral.ttl_hours)),
            )
        if "containment" in d:
            cfg.containment = ContainmentConfig(
                protected_roots=list(d["containment"].get("protected_roots", []))
            )
        if "push_guard" in d:
            cfg.push_guard = PushGuardConfig(
                enabled=bool(d["push_guard"].get("enabled", True))
            )
        if "secret_filter" in d:
            s = d["secret_filter"]
            cfg.secret_filter = SecretFilterConfig(
                enabled=bool(s.get("enabled", True)),
                action=str(s.get("action", "mask")),
                builtin_rules=bool(s.get("builtin_rules", True)),
                extra_rules=list(s.get("extra_rules", [])),
                max_file_bytes=int(s.get("max_file_bytes", 2_000_000)),
            )
        if "credentials" in d:
            cfg.credentials = CredentialsConfig(
                source=str(d["credentials"].get("source", "env"))
            )
        if "repositories" in d:
            r = dict(d["repositories"])
            allow_any = bool(r.pop("allow_any", False))
            entries: dict[str, RepoEntry] = {}
            for name, spec in r.items():
                if isinstance(spec, dict) and "url" in spec:
                    entries[name] = RepoEntry(
                        url=spec["url"],
                        allowed_refs=list(spec.get("allowed_refs", [])),
                    )
            cfg.repositories = RepositoriesConfig(allow_any=allow_any, entries=entries)
        if "audit_log" in d:
            cfg.audit_log = AuditConfig(path=d["audit_log"].get("path"))
        cfg.validate()
        return cfg

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "Config":
        path = path or os.environ.get("GITBORROW_CONFIG")
        if not path and os.path.isfile("gitborrow.toml"):
            path = "gitborrow.toml"
        if not path:
            cfg = Config()
            cfg.validate()
            return cfg
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        return Config.from_dict(data)

    # -- validation --------------------------------------------------------
    def validate(self) -> None:
        if self.secret_filter.action not in ("mask", "delete", "fail"):
            raise ValueError(f"invalid secret_filter.action: {self.secret_filter.action}")
        if self.credentials.source not in ("env", "keyring", "file"):
            raise ValueError(f"invalid credentials.source: {self.credentials.source}")
        if self.ephemeral.ttl_hours < 0:
            raise ValueError("ephemeral.ttl_hours must be >= 0")
