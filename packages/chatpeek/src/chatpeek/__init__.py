"""chatpeek — a read-only chat connector built on agent-policy-kit.

A thin instance of `agent_policy_kit.ReadOnlyConnector`: list / read / search /
download over a pluggable chat backend, gated by a fail-closed channel allowlist,
with every call audited and results secret-masked. Read-only (no post / edit /
delete).
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
