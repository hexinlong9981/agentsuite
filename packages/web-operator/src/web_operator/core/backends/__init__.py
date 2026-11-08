"""Pluggable browser backends."""

from __future__ import annotations

from abc import ABC, abstractmethod


class BrowserBackend(ABC):
    @abstractmethod
    def dispatch(self, operation: str, **params):
        """Perform the operation and return its result."""

    def describe(self, operation: str, **params) -> str:
        """A human-readable preview of what a mutate operation will do."""
        return f"{operation}({params})"

    def close(self) -> None:  # pragma: no cover - trivial
        pass


from .fake import FakeBackend  # noqa: E402,F401
