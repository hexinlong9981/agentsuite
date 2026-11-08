"""Pluggable, read-only mail backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..errors import BackendError


class MailBackend(ABC):
    @abstractmethod
    def list_folders(self) -> list[str]: ...

    @abstractmethod
    def list_messages(self, folder: str) -> list[dict]: ...

    @abstractmethod
    def read_message(self, folder: str, message_id: str) -> dict: ...

    @abstractmethod
    def search(self, folder: str, query: str) -> list[dict]: ...

    @abstractmethod
    def download_attachment(self, folder: str, message_id: str, name: str) -> bytes: ...


_REGISTRY: dict[str, type] = {}


def register(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def make_backend(backend_type: str, options: dict) -> MailBackend:
    if backend_type not in _REGISTRY:
        raise BackendError(f"no such mail backend: {backend_type!r} (available: {sorted(_REGISTRY)})")
    return _REGISTRY[backend_type](**options)


from .fake import FakeMailBackend  # noqa: E402
from .imap import ImapBackend  # noqa: E402

register("fake", FakeMailBackend)
register("imap", ImapBackend)
