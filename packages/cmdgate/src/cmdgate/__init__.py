"""cmdgate — run only allowlisted commands on allowlisted targets.

A command is executed only when it matches an allow rule in a policy that
applies to the chosen target. Everything else is denied (fail-closed). By
default policies are read-only: irreversible programs are refused even if a
rule would match. Commands run as an argv vector (no shell). Every run and
every denial is audited (with the reason).
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
