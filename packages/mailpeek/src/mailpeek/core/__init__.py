"""Public core API."""

from __future__ import annotations

from agent_policy_kit import OperationNotAllowed, ScopeDenied

from .backends import MailBackend, make_backend
from .config import Config
from .connector import MailboxConnector
from .errors import BackendError, MailpeekError

__all__ = [
    "Config", "MailboxConnector", "MailBackend", "make_backend",
    "MailpeekError", "BackendError", "ScopeDenied", "OperationNotAllowed",
]
