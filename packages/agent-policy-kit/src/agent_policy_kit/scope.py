"""Fail-closed scope policy: a resource x operation matrix.

Operations are split into read and write kinds. A read-only policy refuses every
write operation regardless of the matrix; to permit a write you must set
``read_only=False`` *and* list that operation for the resource.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from .errors import OperationNotAllowed, ScopeDenied

READ_OPERATIONS = frozenset({"navigate", "list", "read", "search", "download"})
WRITE_OPERATIONS = frozenset({"create", "update", "delete", "send", "mutate"})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    code: str = "ok"            # ok | scope | operation | readonly


class ScopePolicy:
    def __init__(self, resources: dict[str, set[str]] | None = None, *,
                 read_only: bool = True, allow_unlisted: bool = False) -> None:
        self.resources: dict[str, set[str]] = {k: set(v) for k, v in (resources or {}).items()}
        self.read_only = read_only
        self.allow_unlisted = allow_unlisted

    def _match(self, resource: str) -> set[str] | None:
        if resource in self.resources:
            return self.resources[resource]
        for pattern, ops in self.resources.items():
            if fnmatch.fnmatch(resource, pattern):
                return ops
        return None

    def check(self, resource: str, operation: str) -> Decision:
        if self.read_only and operation in WRITE_OPERATIONS:
            return Decision(False, f"operation {operation!r} is a write and the policy is read-only", "readonly")
        ops = self._match(resource)
        if ops is None:
            if self.allow_unlisted and (operation in READ_OPERATIONS or not self.read_only):
                return Decision(True, "allowed (unlisted resource; allow_unlisted=True)")
            if self.allow_unlisted:
                return Decision(False, f"operation {operation!r} not permitted", "operation")
            return Decision(False, f"resource {resource!r} is not in scope (fail-closed)", "scope")
        if operation not in ops:
            return Decision(False,
                            f"operation {operation!r} not allowed on {resource!r} (allowed: {sorted(ops)})",
                            "operation")
        return Decision(True, f"allowed: {resource}/{operation}")

    def require(self, resource: str, operation: str) -> Decision:
        d = self.check(resource, operation)
        if d.allowed:
            return d
        if d.code == "scope":
            raise ScopeDenied(d.reason)
        raise OperationNotAllowed(d.reason)
