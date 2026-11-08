"""agent-policy-kit — building blocks for safe, read-only agent integrations.

- ScopePolicy       : fail-closed resource x operation matrix (read-only default)
- SecretMasker      : mask secrets in text and structured results
- AuditLog          : JSON-lines audit of allows and denials
- ReadOnlyConnector : base class so a provider (Slack/Box/mailbox/issue tracker/…)
                      becomes a thin, read-only, policed, audited, masked instance
- KitConfig         : assemble the three from TOML/dict
"""

from __future__ import annotations

from .approval import AllowAll, Approval, Callback, DenyAll
from .audit import AuditLog
from .config import KitConfig
from .connector import ReadOnlyConnector
from .cli import exit_code_for
from .credentials import (
    CredentialHandle,
    CredentialStore,
    EnvPassthroughStore,
    FileCredentialStore,
    NullCredentialStore,
)
from .errors import (
    OperationNotAllowed,
    OperationNotApproved,
    OtpError,
    PolicyKitError,
    ScopeDenied,
)
from .otp import CallbackOtpProvider, CommandOtpProvider, OtpProvider, TotpProvider
from .paths import PathEscape, contained_join, is_under, safe_name
from .scope import READ_OPERATIONS, WRITE_OPERATIONS, Decision, ScopePolicy
from .secrets import SecretMasker
from .ssh import build_ssh_argv

__version__ = "0.1.0"

__all__ = [
    "ScopePolicy", "Decision", "READ_OPERATIONS", "WRITE_OPERATIONS",
    "SecretMasker", "AuditLog", "ReadOnlyConnector", "KitConfig",
    "contained_join", "safe_name", "is_under", "PathEscape",
    "exit_code_for",
    "Approval", "DenyAll", "AllowAll", "Callback",
    "CredentialHandle", "CredentialStore", "NullCredentialStore", "EnvPassthroughStore", "FileCredentialStore",
    "OtpProvider", "TotpProvider", "CallbackOtpProvider", "CommandOtpProvider",
    "build_ssh_argv",
    "PolicyKitError", "ScopeDenied", "OperationNotAllowed", "OperationNotApproved", "OtpError",
]
