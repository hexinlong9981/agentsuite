"""remote-file-sync — pull files from a source into local destinations by policy.

- the source is read-only (files are only *read* from it, never written)
- each file is classified (e.g. config vs data) and routed to a destination root
- writes are contained to configured destination roots (fail-closed; no traversal)
- sources and policies are allowlists; audit records every pull
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
