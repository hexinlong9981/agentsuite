"""gitborrow — fetch source into disposable, read-only, agent-safe checkouts.

Borrow a repository into an ephemeral (tmpfs) checkout, reference it, then
return (discard) it. Four independent guarantees:

  1. Containment / ephemerality  — source lives only in a disposable area.
  2. Push guard (toggleable)      — no accidental writes back to a remote.
  3. Secret filtering             — secrets stripped before a consumer reads.
  4. Credential isolation         — fetch credentials never reach the consumer.

The public API lives in :mod:`gitborrow.core`.
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
