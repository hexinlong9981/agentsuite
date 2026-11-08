"""term-operator — policy-gated interactive terminal/console automation.

Drive an interactive terminal session under policy: reading the screen/output is
a read operation; sending a command is a write that must match a command
allowlist (reused from `cmdgate`) and be approved (preview -> human). Sessions
are a fail-closed allowlist and every action is audited. Backends are pluggable:
a `fake` backend ships for offline use, a `local_pty` backend drives a real PTY,
and SSH-interactive / GUI-terminal-window clients (e.g. an RLogin-style app) fit
as additional backends.
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
