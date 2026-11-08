"""GUI terminal window backend (e.g. for Windows/X11 GUI terminal emulators).

Controls a GUI terminal window (such as RLogin, TeraTerm, or an X11 terminal)
generically by delegating keystrokes/commands through a configurable send helper
and reading screen/output from a session log or capture helper.

All window identifiers, helper commands, and log paths come from configuration
at runtime — no application-specific or environment-specific logic is hardcoded.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import BackendError
from . import TerminalBackend

RunnerFunc = Callable[[list[str]], subprocess.CompletedProcess[bytes]]


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, capture_output=True, check=True)


class GuiWindowBackend(TerminalBackend):
    """Generic GUI terminal window driver."""

    def __init__(
        self,
        *,
        window_title_template: str = "{session}",
        send_mode: str = "command",      # "command" | "mock"
        send_cmd_template: list[str] | None = None,
        log_dir: str | Path | None = None,
        runner: RunnerFunc | None = None,
    ) -> None:
        self.window_title_template = window_title_template
        self.send_mode = send_mode
        self.send_cmd_template = send_cmd_template or ["term-send", "{window_title}", "{command}"]
        self.log_dir = Path(log_dir) if log_dir else None
        self._runner = runner or _default_runner

        self._sessions: dict[str, dict[str, Any]] = {}
        # In-memory buffer for mock mode or cached readings
        self._mock_buffers: dict[str, list[str]] = {}

    def _get_window_title(self, session: str) -> str:
        return self.window_title_template.format(session=session)

    def open(self, session: str) -> None:
        """Open or attach to a GUI terminal session."""
        win_title = self._get_window_title(session)
        self._sessions[session] = {
            "window_title": win_title,
            "status": "attached",
        }
        if session not in self._mock_buffers:
            self._mock_buffers[session] = [f"Attached to {win_title}\n"]

    def read(self, session: str) -> str:
        """Read recent output from log file or mock buffer."""
        if session not in self._sessions:
            raise BackendError(f"session {session!r} is not open")

        # If a log_dir is configured, read from session log file if present
        if self.log_dir:
            log_file = self.log_dir / f"{session}.log"
            if log_file.is_file():
                try:
                    # Read last 4KB to avoid unbounded memory explosion
                    with open(log_file, "rb") as f:
                        f.seek(0, os.SEEK_END)
                        size = f.tell()
                        f.seek(max(0, size - 4096))
                        return f.read().decode("utf-8", errors="replace")
                except Exception as exc:
                    raise BackendError(f"could not read log for {session}: {exc}") from exc

        # Fallback to in-memory buffer
        return "".join(self._mock_buffers.get(session, []))

    def send(self, session: str, command: str) -> str:
        """Send a command string to the target GUI terminal window."""
        if session not in self._sessions:
            raise BackendError(f"session {session!r} is not open")

        win_title = self._sessions[session]["window_title"]

        if self.send_mode == "mock":
            output = f"> {command}\n[mock output for {command}]\n"
            self._mock_buffers.setdefault(session, []).append(output)
            return output

        if self.send_mode == "command":
            # Format command helper template
            formatted_argv = [
                arg.format(session=session, window_title=win_title, command=command)
                for arg in self.send_cmd_template
            ]
            try:
                res = self._runner(formatted_argv)
                out = res.stdout.decode("utf-8", errors="replace")
                self._mock_buffers.setdefault(session, []).append(f"> {command}\n{out}")
                return out
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                raise BackendError(f"GUI send helper failed for {session}: {exc}") from exc

        raise BackendError(f"unknown send_mode: {self.send_mode!r}")

    def close(self, session: str) -> None:
        """Close or detach the GUI terminal session."""
        self._sessions.pop(session, None)
        self._mock_buffers.pop(session, None)
