"""Session x operation matrix, built on agent_policy_kit.ScopePolicy.

Operations: ``open`` / ``read`` / ``close`` (read-side) and ``send`` (write).
``send`` is permitted here only when listed; the command allowlist and human
approval are enforced separately by the Operator, so this layer is not
``read_only`` in the kit's sense (which would block ``send`` outright).
"""

from __future__ import annotations

from agent_policy_kit import ScopePolicy
from agent_policy_kit.scope import Decision

from .errors import OperationNotAllowed, SessionNotAllowed

CATEGORIES = frozenset({"open", "read", "close", "send"})
OP_TO_CATEGORY = {"open": "open", "read": "read", "close": "close", "send": "send"}


class SessionPolicy(ScopePolicy):
    def __init__(self, sessions: dict[str, set[str]] | None = None) -> None:
        super().__init__(sessions, read_only=False, allow_unlisted=False)

    @property
    def sessions(self) -> dict[str, set[str]]:
        return self.resources

    def require(self, session: str, operation: str) -> Decision:
        d = self.check(session, operation)
        if d.allowed:
            return d
        if d.code == "scope":
            raise SessionNotAllowed(d.reason)
        raise OperationNotAllowed(d.reason)
