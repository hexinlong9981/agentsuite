"""Host x operation-category matrix, built on agent_policy_kit.ScopePolicy.

The host matrix is a scope policy where resources are hosts and operations are
categories. "mutate" is permitted when listed; the human approval for mutations
is enforced separately by the Operator, so the scope layer itself is not
"read_only" in the kit's sense (that would block mutate outright).
"""

from __future__ import annotations

from agent_policy_kit import ScopePolicy
from agent_policy_kit.scope import Decision

from .errors import HostNotAllowed, OperationNotAllowed

CATEGORIES = frozenset({"navigate", "read", "download", "interact", "mutate"})

# Operation name -> category. "mutate" always also needs human approval.
OP_TO_CATEGORY = {
    "navigate": "navigate",
    "read": "read",
    "get_elements": "read",
    "screenshot": "read",
    "download": "download",
    "fill": "interact",
    "select": "interact",
    "click": "interact",
    "submit": "mutate",
}


class HostPolicy(ScopePolicy):
    def __init__(self, hosts: dict[str, set[str]] | None = None) -> None:
        super().__init__(hosts, read_only=False, allow_unlisted=False)

    @property
    def hosts(self) -> dict[str, set[str]]:
        return self.resources

    def require(self, host: str, category: str) -> Decision:
        d = self.check(host, category)
        if d.allowed:
            return d
        if d.code == "scope":
            raise HostNotAllowed(d.reason)
        raise OperationNotAllowed(d.reason)
