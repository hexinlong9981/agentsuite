"""Public core API."""

from __future__ import annotations

from agent_policy_kit import AllowAll, Approval, Callback, DenyAll

from .backends import TerminalBackend, make_backend
from .config import Config
from .errors import (
    BackendError,
    CommandNotAllowed,
    OperationNotAllowed,
    SendNotApproved,
    SessionNotAllowed,
    TermOperatorError,
)
from .operator import TermOperator
from .policy import CATEGORIES, OP_TO_CATEGORY, SessionPolicy

__all__ = [
    "Config", "TermOperator", "SessionPolicy", "TerminalBackend", "make_backend",
    "CATEGORIES", "OP_TO_CATEGORY",
    "Approval", "DenyAll", "AllowAll", "Callback",
    "TermOperatorError", "SessionNotAllowed", "OperationNotAllowed",
    "CommandNotAllowed", "SendNotApproved", "BackendError",
]
