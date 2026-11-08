"""agentflow — record an AI agent's run as steps, and render them.

A `Recorder` appends events (step start/end, notes, tool calls, errors) to an
append-only ``events.jsonl`` under a per-run directory; event data is passed
through the shared `SecretMasker` first, so the run log carries no secrets.
A `RunStore` lists and loads runs (contained under the root), and `view` renders
a run as text or a self-contained HTML timeline. This is observability, not an
access gate — it records what an agent did.
"""

from .core import __all__ as _core_all  # noqa: F401

__version__ = "0.1.0"
