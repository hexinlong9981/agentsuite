"""Public core API."""

from __future__ import annotations

from agent_policy_kit import OperationNotAllowed, ScopeDenied

from .backends import FileStoreBackend, make_backend
from .config import Config
from .connector import FileStoreConnector
from .errors import BackendError, FilepeekError

__all__ = [
    "Config", "FileStoreConnector", "FileStoreBackend", "make_backend",
    "FilepeekError", "BackendError", "ScopeDenied", "OperationNotAllowed",
]
