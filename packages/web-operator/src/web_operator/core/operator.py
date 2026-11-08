"""Operator — the policy + approval + audit gate in front of a backend."""

from __future__ import annotations

from .approval import Approval, DenyAll
from .audit import AuditLog
from .backends import BrowserBackend
from .errors import InvalidOperation, MutationNotApproved
from .policy import OP_TO_CATEGORY, HostPolicy


class Operator:
    def __init__(self, *, backend: BrowserBackend, policy: HostPolicy,
                 approval: Approval | None = None, audit: AuditLog | None = None,
                 name: str = "web-operator") -> None:
        self.backend = backend
        self.policy = policy
        self.approval = approval or DenyAll()
        self.audit = audit or AuditLog(None)
        self.name = name

    def operate(self, host: str, operation: str, **params):
        if operation not in OP_TO_CATEGORY:
            raise InvalidOperation(f"unknown operation {operation!r}")
        category = OP_TO_CATEGORY[operation]

        # fail-closed matrix check (raises HostNotAllowed / OperationNotAllowed)
        try:
            self.policy.require(host, category)
        except Exception as exc:
            self.audit.record({"action": "operate", "host": host, "operation": operation,
                               "result": "denied", "reason": type(exc).__name__})
            raise

        if category == "mutate":
            preview = self.backend.describe(operation, **params)
            approved = self.approval.request(host, operation, preview)
            self.audit.record({"action": "mutate_request", "host": host, "operation": operation,
                               "approved": approved, "preview": preview})
            if not approved:
                raise MutationNotApproved(f"mutation not approved: {preview}")

        result = self.backend.dispatch(operation, **params)
        self.audit.record({"action": "operate", "host": host, "operation": operation, "result": "ok"})
        return result

    # convenience wrappers
    def navigate(self, host: str, url: str):
        return self.operate(host, "navigate", url=url)

    def read(self, host: str):
        return self.operate(host, "read")

    def get_elements(self, host: str):
        return self.operate(host, "get_elements")

    def fill(self, host: str, selector: str, value: str):
        return self.operate(host, "fill", selector=selector, value=value)

    def click(self, host: str, selector: str):
        return self.operate(host, "click", selector=selector)

    def submit(self, host: str, selector: str):
        return self.operate(host, "submit", selector=selector)

    def close(self) -> None:
        self.backend.close()
