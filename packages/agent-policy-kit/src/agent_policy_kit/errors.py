"""Exception hierarchy."""

from __future__ import annotations


class PolicyKitError(Exception):
    """Base class."""


class ScopeDenied(PolicyKitError):
    """The resource is not in scope (fail-closed)."""


class OperationNotAllowed(PolicyKitError):
    """The operation is not permitted on this resource (e.g. a write under read-only)."""


class OperationNotApproved(PolicyKitError):
    """A write/mutating operation was allowed by scope but not approved."""


class OtpError(PolicyKitError):
    """An OTP could not be produced (missing secret, failed command, …).

    Never contains the secret or the code.
    """
