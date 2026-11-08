"""Public core API — the single place both adapters (CLI, MCP) call.

All guarantees (the four pillars) live here, so they do not depend on which
entry point is used.
"""

from __future__ import annotations

from .config import Config
from .credentials import CredentialStore, EnvCredentialStore, NullCredentialStore, make_store
from .errors import (
    ContainmentError,
    CredentialError,
    FetchTimeout,
    FilterError,
    GitborrowError,
    GuardViolation,
    MirrorUnreachable,
    RefNotAllowed,
    RepositoryNotAllowed,
)
from .lifecycle import fetch, list_checkouts, purge, release, status
from .types import (
    Checkout,
    FetchRequest,
    FetchResult,
    MaskReport,
    PurgeResult,
    ReleaseResult,
    Status,
    citation,
)

__all__ = [
    "Config",
    "CredentialStore", "EnvCredentialStore", "NullCredentialStore", "make_store",
    "fetch", "release", "purge", "list_checkouts", "status",
    "FetchRequest", "FetchResult", "Checkout", "MaskReport",
    "ReleaseResult", "PurgeResult", "Status", "citation",
    "GitborrowError", "ContainmentError", "GuardViolation", "RepositoryNotAllowed",
    "RefNotAllowed", "CredentialError", "FilterError", "MirrorUnreachable", "FetchTimeout",
]
