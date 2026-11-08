"""In-memory fake terminal for demos and offline tests (deterministic)."""

from __future__ import annotations

from ..errors import BackendError
from . import TerminalBackend


class FakeTerminal(TerminalBackend):
    def __init__(self) -> None:
        self.buffers: dict[str, str] = {}
        self.sent: dict[str, list[str]] = {}

    def open(self, session: str):
        self.buffers.setdefault(session, f"[session {session} opened]\n")
        self.sent.setdefault(session, [])
        return {"opened": session}

    def read(self, session: str) -> str:
        if session not in self.buffers:
            raise BackendError(f"session not open: {session}")
        return self.buffers[session]

    def send(self, session: str, command: str) -> str:
        if session not in self.buffers:
            raise BackendError(f"session not open: {session}")
        self.sent[session].append(command)
        output = f"$ {command}\n[ok]\n"
        self.buffers[session] += output
        return output

    def close(self, session: str):
        self.buffers.pop(session, None)
        self.sent.pop(session, None)
        return {"closed": session}
