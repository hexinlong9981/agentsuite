"""Pluggable read-only transports."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import Source
from ..errors import TransportError


class Transport(ABC):
    @abstractmethod
    def list(self) -> list[str]: ...

    @abstractmethod
    def read(self, relpath: str) -> bytes: ...


def make_transport(source: Source) -> Transport:
    if source.transport == "local":
        if not source.root:
            raise TransportError("local source requires 'root'")
        from .local import LocalTransport
        return LocalTransport(source.root)
    if source.transport == "ssh":
        from .ssh import SshTransport
        return SshTransport(source)
    raise TransportError(f"unknown or unsupported transport: {source.transport!r}")
