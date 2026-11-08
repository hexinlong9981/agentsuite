"""Pluggable terminal backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..errors import BackendError


class TerminalBackend(ABC):
    @abstractmethod
    def open(self, session: str): ...

    @abstractmethod
    def read(self, session: str) -> str: ...

    @abstractmethod
    def send(self, session: str, command: str) -> str: ...

    @abstractmethod
    def close(self, session: str): ...


_REGISTRY: dict[str, type] = {}


def register(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def make_backend(backend_type: str, options: dict) -> TerminalBackend:
    if backend_type not in _REGISTRY:
        raise BackendError(f"no such terminal backend: {backend_type!r} (available: {sorted(_REGISTRY)})")
    return _REGISTRY[backend_type](**options)


from .fake import FakeTerminal  # noqa: E402
from .gui_window import GuiWindowBackend  # noqa: E402
from .local_pty import LocalPtyBackend  # noqa: E402
from .ssh import SshBackend  # noqa: E402

register("fake", FakeTerminal)
register("gui_window", GuiWindowBackend)
register("local_pty", LocalPtyBackend)
register("ssh", SshBackend)
