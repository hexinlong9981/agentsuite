"""Configuration — the fail-closed tool manifest and audit (TOML/dict)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from agent_policy_kit import AuditLog

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class Config:
    enabled: set[str] = field(default_factory=set)       # the manifest: tool names to expose
    audit: AuditLog = field(default_factory=lambda: AuditLog(None))

    @staticmethod
    def from_dict(d: dict) -> "Config":
        d = d or {}
        enabled = {str(n) for n in ((d.get("tools") or {}).get("enabled", []))}
        audit = AuditLog((d.get("audit") or {}).get("path"))
        return Config(enabled=enabled, audit=audit)

    @staticmethod
    def load(path: str | os.PathLike | None = None) -> "Config":
        path = path or os.environ.get("MCP_RUNTIME_CONFIG")
        if not path and os.path.isfile("mcp_runtime.toml"):
            path = "mcp_runtime.toml"
        if not path:
            return Config.from_dict({})
        if tomllib is None:  # pragma: no cover
            raise RuntimeError("tomllib unavailable; Python >= 3.11 required to read TOML")
        with open(path, "rb") as fh:
            return Config.from_dict(tomllib.load(fh))
