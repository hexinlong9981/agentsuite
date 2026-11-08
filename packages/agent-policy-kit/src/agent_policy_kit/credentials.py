"""Credential isolation (shared core).

A store resolves to a :class:`CredentialHandle` that carries *only* environment
additions for a subprocess. Secret values are never returned to the caller,
printed, logged, or placed on a command line — the handle hands them straight to
the subprocess environment and nowhere else.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CredentialHandle:
    """Opaque handle. ``env`` is merged into a subprocess environment only."""

    env: dict = field(default_factory=dict)


class CredentialStore(ABC):
    @abstractmethod
    def resolve(self, target: str) -> CredentialHandle: ...


class NullCredentialStore(CredentialStore):
    def resolve(self, target: str) -> CredentialHandle:
        return CredentialHandle(env={})


class EnvPassthroughStore(CredentialStore):
    """Pass named environment variables into the subprocess env without exposing
    their values to the caller.

    ``spec`` maps a subprocess env-var name to the source env-var to read it from
    (e.g. ``{"GIT_TOKEN": "MY_CI_TOKEN"}``). Missing sources are simply skipped.
    """

    def __init__(self, spec: dict[str, str]) -> None:
        self.spec = dict(spec)

    def resolve(self, target: str) -> CredentialHandle:
        env: dict[str, str] = {}
        for dest, src in self.spec.items():
            value = os.environ.get(src)
            if value is not None:
                env[dest] = value
        return CredentialHandle(env=env)


class FileCredentialStore(CredentialStore):
    """Load credentials from an isolated file outside the repository.

    The file contains key-value pairs (key=value, or JSON object), keeping secret
    credentials completely out of source code and AI-visible contexts.
    At runtime, secret values are populated directly into the subprocess
    environment without exposing them to the agent.

    Optional strict_permissions ensures the file is not world-readable (mode & 0o077 == 0).
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        target_map: dict[str, dict[str, str]] | None = None,
        strict_permissions: bool = False,
    ) -> None:
        self.path = os.fspath(path)
        self.target_map = target_map or {}
        self.strict_permissions = strict_permissions

    def _read_store(self) -> dict[str, str]:
        p = os.path.abspath(self.path)
        if not os.path.isfile(p):
            return {}

        if self.strict_permissions and hasattr(os, "stat"):
            mode = os.stat(p).st_mode
            if mode & 0o077 != 0:
                raise PermissionError(
                    f"credential file {self.path!r} has insecure permissions: mode={oct(mode)}"
                )

        secrets: dict[str, str] = {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                # Support simple KEY=VAL format or JSON
                if content.startswith("{") and content.endswith("}"):
                    import json

                    data = json.loads(content)
                    if isinstance(data, dict):
                        return {str(k): str(v) for k, v in data.items()}
                for line in content.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        secrets[k.strip()] = v.strip().strip("\"'")
        except Exception:
            return {}
        return secrets

    def resolve(self, target: str) -> CredentialHandle:
        raw_secrets = self._read_store()
        env: dict[str, str] = {}

        # If target has an explicit mapping (dest_env -> secret_key)
        if target in self.target_map:
            mapping = self.target_map[target]
            for dest_env, secret_key in mapping.items():
                if secret_key in raw_secrets:
                    env[dest_env] = raw_secrets[secret_key]
        else:
            # By default, pass matching raw secrets into subprocess env
            env = dict(raw_secrets)

        return CredentialHandle(env=env)

