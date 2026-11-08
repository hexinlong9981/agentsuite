"""Configuration: the session allowlist, the send-command allowlist (reused from
cmdgate), the backend, and audit (TOML/dict).

No credentials/hosts live here for a real backend; those come from the backend's
own options / the environment at run time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from agent_policy_kit import AuditLog, SecretMasker
from cmdgate.core import Policy as CommandPolicy
from cmdgate.core import Rule as CommandRule

from .policy import SessionPolicy

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class Config:
    backend_type: str = "fake"
    backend_options: dict = field(default_factory=dict)
    scope: SessionPolicy = field(default_factory=SessionPolicy)
    command_policy: CommandPolicy = field(
        default_factory=lambda: CommandPolicy(name="send", targets=[], read_only=True, allow=[]))
    audit: AuditLog = field(default_factory=lambda: AuditLog(None))
    masker: SecretMasker = field(default_factory=SecretMasker)

    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = d or {}
        sessions = {s: set(ops) for s, ops in (d.get("sessions") or {}).items()}
        scope = SessionPolicy(sessions)

        cmds = d.get("commands") or {}
        rules = [CommandRule(program=r["program"], args_regex=r.get("args_regex"))
                 for r in cmds.get("allow", []) if isinstance(r, dict) and "program" in r]
        command_policy = CommandPolicy(
            name="send", targets=[],
            read_only=bool(cmds.get("read_only", True)),
            allow_shell=bool(cmds.get("allow_shell", False)),
            allow=rules,
        )

        backend = dict(d.get("backend") or {})
        backend_type = backend.pop("type", "fake")
        audit = AuditLog((d.get("audit") or {}).get("path"))
        return Config(backend_type=backend_type, backend_options=backend, scope=scope,
                      command_policy=command_policy, audit=audit, masker=SecretMasker())

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "Config":
        path = path or os.environ.get("TERM_OPERATOR_CONFIG")
        if not path and os.path.isfile("term_operator.toml"):
            path = "term_operator.toml"
        if not path:
            return Config.from_dict({})
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            return Config.from_dict(tomllib.load(fh))
