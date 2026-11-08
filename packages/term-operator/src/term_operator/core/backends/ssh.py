"""SSH interactive backend — drive a remote shell over SSH, via a bastion chain,
auto-answering an OTP prompt. Read-side by default; writes are gated by the
operator (allowlist + approval).

Connection control: the command is assembled with the shared ``build_ssh_argv``
(so jump/bastion hosts become ``-J h1,h2``). If ``otp_env`` is set, a TOTP code is
computed from the secret in that env var (never stored here) and written when a
2FA / one-time-code prompt appears.

Live only — needs a real SSH endpoint; not exercised by the offline test suite.
The command assembly (``ssh_argv``) is pure and is covered.
"""

from __future__ import annotations

import os
import re
import select
import time

from agent_policy_kit import TotpProvider, build_ssh_argv

from ..errors import BackendError
from . import TerminalBackend

_OTP_PROMPT = re.compile(r"(one.?time|verification|otp|2fa|token|passcode)", re.I)
_PW_PROMPT = re.compile(r"password:", re.I)


class SshBackend(TerminalBackend):
    def __init__(self, host: str, user: str | None = None, port: int | None = None,
                 jump: list[str] | None = None, ssh_options: list[str] | None = None,
                 otp_env: str | None = None, read_timeout: float = 1.0) -> None:
        self.ssh_argv = build_ssh_argv(host, user=user, port=port, jump=jump,
                                       options=ssh_options)
        self.otp_env = otp_env
        self.read_timeout = float(read_timeout)
        self._fd: int | None = None
        self._pid: int | None = None

    def _otp_code(self) -> str | None:
        if not self.otp_env:
            return None
        return TotpProvider(self.otp_env).code()

    def open(self, session: str):  # pragma: no cover - needs a live SSH endpoint
        import pty
        if self._fd is not None:
            return {"opened": session}
        pid, fd = pty.fork()
        if pid == 0:
            os.execvp(self.ssh_argv[0], self.ssh_argv)
            os._exit(1)
        self._pid, self._fd = pid, fd
        time.sleep(0.2)
        banner = self._drain()
        if (_OTP_PROMPT.search(banner) or _PW_PROMPT.search(banner)):
            code = self._otp_code()
            if code:
                os.write(fd, (code + "\n").encode())
                banner += self._drain()
        return {"opened": session}

    def _drain(self) -> str:  # pragma: no cover - needs a live SSH endpoint
        assert self._fd is not None
        chunks = []
        deadline = time.time() + self.read_timeout
        while time.time() < deadline:
            r, _, _ = select.select([self._fd], [], [], self.read_timeout)
            if not r:
                break
            try:
                data = os.read(self._fd, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data.decode(errors="replace"))
        return "".join(chunks)

    def read(self, session: str) -> str:  # pragma: no cover - needs a live SSH endpoint
        if self._fd is None:
            raise BackendError("session not open")
        return self._drain()

    def send(self, session: str, command: str) -> str:  # pragma: no cover - needs a live SSH endpoint
        if self._fd is None:
            raise BackendError("session not open")
        os.write(self._fd, (command + "\n").encode())
        return self._drain()

    def close(self, session: str):  # pragma: no cover - needs a live SSH endpoint
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            try:
                os.waitpid(self._pid, os.WNOHANG)
            except OSError:
                pass
            self._fd = self._pid = None
        return {"closed": session}
