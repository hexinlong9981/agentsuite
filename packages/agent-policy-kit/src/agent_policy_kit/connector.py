"""Policed connector base.

A provider implements :meth:`_dispatch`. Every call goes through the scope policy
(fail-closed) and is audited; read results are run through the secret masker.

Write operations (the kit's ``WRITE_OPERATIONS``: create / update / delete / send
/ mutate) are additionally gated by an approval hook (default: deny all) and are
refused unless the scope is not read-only *and* lists the operation. A connector
that never enables writes stays effectively read-only (the historical behaviour).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .approval import Approval, DenyAll
from .audit import AuditLog
from .errors import OperationNotApproved
from .scope import WRITE_OPERATIONS, ScopePolicy
from .secrets import SecretMasker


class ReadOnlyConnector(ABC):
    def __init__(self, *, scope: ScopePolicy, audit: AuditLog | None = None,
                 masker: SecretMasker | None = None, approval: Approval | None = None,
                 name: str = "connector") -> None:
        self.scope = scope
        self.audit = audit or AuditLog(None)
        self.masker = masker
        self.approval = approval or DenyAll()
        self.name = name

    def operate(self, resource: str, operation: str, **params):
        decision = self.scope.check(resource, operation)
        if not decision.allowed:
            self.audit.deny(decision.reason, connector=self.name,
                            resource=resource, operation=operation)
            self.scope.require(resource, operation)   # re-evaluates and raises a typed error

        if operation in WRITE_OPERATIONS:
            preview = self.preview(resource, operation, params)
            if not self.approval.request(resource, operation, preview):
                self.audit.deny("not approved", connector=self.name,
                                resource=resource, operation=operation)
                raise OperationNotApproved(f"{operation} on {resource!r} not approved: {preview}")

        self.audit.allow(connector=self.name, resource=resource, operation=operation)
        result = self._dispatch(resource, operation, **params)
        return self.masker.mask_obj(result) if self.masker else result

    def preview(self, resource: str, operation: str, params: dict) -> str:
        """Human-readable preview of a write, shown to the approval hook. Override
        to redact or format; the default is a compact description."""
        return f"{operation} on {resource} ({params})"

    # read convenience
    def list(self, resource: str, **params):
        return self.operate(resource, "list", **params)

    def read(self, resource: str, **params):
        return self.operate(resource, "read", **params)

    def search(self, resource: str, **params):
        return self.operate(resource, "search", **params)

    def download(self, resource: str, **params):
        return self.operate(resource, "download", **params)

    # write convenience (approval-gated; refused unless the scope enables them)
    def create(self, resource: str, **params):
        return self.operate(resource, "create", **params)

    def update(self, resource: str, **params):
        return self.operate(resource, "update", **params)

    def delete(self, resource: str, **params):
        return self.operate(resource, "delete", **params)

    def send(self, resource: str, **params):
        return self.operate(resource, "send", **params)

    @abstractmethod
    def _dispatch(self, resource: str, operation: str, **params):
        """Provider implementation."""
