"""Pluggable, read-only chat backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..errors import BackendError


class ChatBackend(ABC):
    @abstractmethod
    def list_channels(self) -> list[str]: ...

    @abstractmethod
    def list_messages(self, channel: str) -> list[dict]: ...

    @abstractmethod
    def read_message(self, channel: str, message_id: str) -> dict: ...

    @abstractmethod
    def search(self, channel: str, query: str) -> list[dict]: ...

    @abstractmethod
    def post_message(self, channel: str, text: str) -> dict: ...

    @abstractmethod
    def download_file(self, channel: str, message_id: str, name: str) -> bytes: ...


_REGISTRY: dict[str, type] = {}


def register(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def make_backend(backend_type: str, options: dict) -> ChatBackend:
    if backend_type not in _REGISTRY:
        raise BackendError(f"no such chat backend: {backend_type!r} (available: {sorted(_REGISTRY)})")
    return _REGISTRY[backend_type](**options)


from .fake import FakeChatBackend  # noqa: E402

register("fake", FakeChatBackend)
