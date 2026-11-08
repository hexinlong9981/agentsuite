"""Approval hooks for state-changing (mutate) operations (shared core).

A write/mutating operation shows a preview and asks an :class:`Approval` to
decide. The safe default refuses everything; a callback can route the decision
to a human.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable


class Approval(ABC):
    @abstractmethod
    def request(self, target: str, operation: str, preview: str) -> bool:
        """Return True to allow the operation, False to refuse it."""


class DenyAll(Approval):
    """Default: refuse every mutation (safe default)."""

    def request(self, target: str, operation: str, preview: str) -> bool:
        return False


class AllowAll(Approval):
    """Approve every mutation. For tests / trusted automation only."""

    def request(self, target: str, operation: str, preview: str) -> bool:
        return True


class Callback(Approval):
    """Delegate the decision to a callable (e.g. prompt a human)."""

    def __init__(self, fn: Callable[[str, str, str], bool]) -> None:
        self._fn = fn

    def request(self, target: str, operation: str, preview: str) -> bool:
        return bool(self._fn(target, operation, preview))
