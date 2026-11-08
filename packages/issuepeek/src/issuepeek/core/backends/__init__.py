"""Pluggable, read-only issue-tracker backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..errors import BackendError


class IssueBackend(ABC):
    @abstractmethod
    def list_projects(self) -> list[str]: ...

    @abstractmethod
    def list_issues(self, project: str) -> list[dict]: ...

    @abstractmethod
    def read_issue(self, project: str, issue_id: str) -> dict: ...

    @abstractmethod
    def search(self, project: str, query: str) -> list[dict]: ...

    @abstractmethod
    def download_attachment(self, project: str, issue_id: str, name: str) -> bytes: ...

    @abstractmethod
    def create_issue(self, project: str, fields: dict) -> dict: ...


_REGISTRY: dict[str, type] = {}


def register(name: str, cls: type) -> None:
    _REGISTRY[name] = cls


def make_backend(backend_type: str, options: dict) -> IssueBackend:
    if backend_type not in _REGISTRY:
        raise BackendError(f"no such issue backend: {backend_type!r} (available: {sorted(_REGISTRY)})")
    return _REGISTRY[backend_type](**options)


from .fake import FakeIssueBackend  # noqa: E402

register("fake", FakeIssueBackend)
