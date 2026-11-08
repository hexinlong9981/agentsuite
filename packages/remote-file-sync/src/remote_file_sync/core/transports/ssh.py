"""SSH transport with bastion (jump-host) support (pull-only).

Pulls files from a remote host over SSH without writing back.
Uses agent_policy_kit.ssh to assemble the command argv cleanly.
"""

from __future__ import annotations

import posixpath
import subprocess
from collections.abc import Callable
from typing import Any

from agent_policy_kit.paths import is_under
from agent_policy_kit.ssh import build_ssh_argv

from ..config import Source
from ..errors import TransportError
from . import Transport


RunnerFunc = Callable[[list[str]], subprocess.CompletedProcess[bytes]]


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, capture_output=True, check=True)


class SshTransport(Transport):
    """Transport that reads files from a remote host via SSH."""

    def __init__(
        self,
        source: Source,
        *,
        runner: RunnerFunc | None = None,
    ) -> None:
        if not source.host:
            raise TransportError("ssh source requires 'host'")
        self.source = source
        self.remote_dir = (source.path or source.root or "").rstrip("/")
        if not self.remote_dir:
            raise TransportError("ssh source requires 'path' or 'root'")
        self._runner = runner or _default_runner

    def _build_cmd(self, remote_argv: list[str]) -> list[str]:
        return build_ssh_argv(
            host=self.source.host,  # type: ignore[arg-type]
            user=self.source.user,
            port=self.source.port,
            jump=self.source.jump,
            options=self.source.ssh_options,
            remote_argv=remote_argv,
        )

    def list(self) -> list[str]:
        """List files relative to remote_dir."""
        # Find all regular files under remote_dir
        cmd = self._build_cmd(["find", self.remote_dir, "-type", "f"])
        try:
            res = self._runner(cmd)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise TransportError(f"SSH list failed: {exc}") from exc

        raw_output = res.stdout.decode("utf-8", errors="replace")
        files: list[str] = []
        prefix = self.remote_dir + "/"
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(prefix):
                rel = line[len(prefix):]
            elif line.startswith(self.remote_dir):
                rel = line[len(self.remote_dir):].lstrip("/")
            else:
                rel = line
            if rel:
                files.append(rel)
        return sorted(files)

    def read(self, relpath: str) -> bytes:
        """Read file contents from remote host."""
        # Prevent remote path traversal
        norm = posixpath.normpath(relpath)
        if norm.startswith(("/", "../")) or norm == "..":
            raise TransportError(f"Path traversal rejected: {relpath}")

        full_remote_path = posixpath.join(self.remote_dir, norm)
        cmd = self._build_cmd(["cat", full_remote_path])
        try:
            res = self._runner(cmd)
            return res.stdout
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise TransportError(f"SSH read failed for {relpath}: {exc}") from exc
