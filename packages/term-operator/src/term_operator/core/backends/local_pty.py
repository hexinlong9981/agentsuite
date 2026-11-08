"""Local PTY backend — drives a shell in a pseudo-terminal. Read-side by default.

Real, interactive; not exercised by the offline test suite. A single session
runs one long-lived shell. ``send`` writes a line and returns whatever output is
available shortly after; ``read`` drains pending output.
"""

from __future__ import annotations

import os
import select
import time

from ..errors import BackendError
from . import TerminalBackend


class LocalPtyBackend(TerminalBackend):
    def __init__(self, shell: str | None = None, read_timeout: float = 0.5) -> None:
        self.shell = shell or os.environ.get("SHELL", "/bin/sh")
        self.read_timeout = float(read_timeout)
        self._sessions: dict[str, tuple[int, int]] = {}   # session -> (pid, master_fd)

    def open(self, session: str):  # pragma: no cover - needs a real PTY
        import pty
        if session in self._sessions:
            return {"opened": session}
        pid, master_fd = pty.fork()
        if pid == 0:
            os.execvp(self.shell, [self.shell])
            os._exit(1)
        self._sessions[session] = (pid, master_fd)
        time.sleep(0.1)
        self._drain(master_fd)
        return {"opened": session}

    def _drain(self, fd: int) -> str:  # pragma: no cover - needs a real PTY
        chunks = []
        deadline = time.time() + self.read_timeout
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], self.read_timeout)
            if not r:
                break
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data.decode(errors="replace"))
        return "".join(chunks)

    def read(self, session: str) -> str:  # pragma: no cover - needs a real PTY
        if session not in self._sessions:
            raise BackendError(f"session not open: {session}")
        return self._drain(self._sessions[session][1])

    def send(self, session: str, command: str) -> str:  # pragma: no cover - needs a real PTY
        if session not in self._sessions:
            raise BackendError(f"session not open: {session}")
        fd = self._sessions[session][1]
        os.write(fd, (command + "\n").encode())
        return self._drain(fd)

    def close(self, session: str):  # pragma: no cover - needs a real PTY
        entry = self._sessions.pop(session, None)
        if entry:
            pid, fd = entry
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except OSError:
                pass
        return {"closed": session}
