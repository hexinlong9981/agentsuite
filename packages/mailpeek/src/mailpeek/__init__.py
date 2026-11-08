"""mailpeek — a read-only mailbox connector built on agent-policy-kit.

A thin instance of `agent_policy_kit.ReadOnlyConnector`: list / read / search /
download over a pluggable mail backend, gated by a fail-closed folder allowlist,
with every call audited and results secret-masked. Read-only (no send / delete /
move). The package is named `mailpeek` to avoid colliding with the stdlib
`mailbox` module.
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
