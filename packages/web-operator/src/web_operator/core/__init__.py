"""Public core API."""

from __future__ import annotations

from .approval import AllowAll, Approval, Callback, DenyAll
from .audit import AuditLog
from .backends import BrowserBackend, FakeBackend
from .config import Config
from .errors import (
    BackendError,
    HostNotAllowed,
    InvalidOperation,
    MutationNotApproved,
    OperationNotAllowed,
    WebOperatorError,
)
from .operator import Operator
from .policy import CATEGORIES, OP_TO_CATEGORY, Decision, HostPolicy

__all__ = [
    "Operator", "HostPolicy", "Decision", "Config",
    "Approval", "DenyAll", "AllowAll", "Callback",
    "BrowserBackend", "FakeBackend", "AuditLog",
    "CATEGORIES", "OP_TO_CATEGORY",
    "WebOperatorError", "HostNotAllowed", "OperationNotAllowed",
    "MutationNotApproved", "InvalidOperation", "BackendError",
]
