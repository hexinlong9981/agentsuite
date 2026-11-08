"""Pluggable, read-only file store backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..errors import BackendError


class FileStoreBackend(ABC):
    @abstractmethod
    def list_folders(self) -> list[str]: ...

    @abstractmethod
    def list_files(self, folder: str) -> list[dict]: ...

    @abstractmethod
    def read_file(self, folder: str, file_id: str) -> dict: ...

    @abstractmethod
    def search(self, folder: str, query: str) -> list[dict]: ...

    @abstractmethod
    def download(self, folder: str, file_id: str) -> bytes: ...

    @abstractmethod
    def upload(self, folder: str, name: str, data: bytes) -> dict: ...


_REGISTRY: dict[str, type] = {}


def register(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def make_backend(backend_type: str, options: dict) -> FileStoreBackend:
    if backend_type not in _REGISTRY:
        raise BackendError(
            f"no such file store backend: {backend_type!r} (available: {sorted(_REGISTRY)})")
    return _REGISTRY[backend_type](**options)


from .fake import FakeFileStoreBackend  # noqa: E402

register("fake", FakeFileStoreBackend)
