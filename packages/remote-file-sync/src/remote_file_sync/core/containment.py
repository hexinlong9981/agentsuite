"""Destination containment — writes stay under the destination root.

Uses the shared core (agent_policy_kit.paths) and maps its error to this tool's
``DestinationError`` so callers/tests keep their own exception type.
"""

from __future__ import annotations

from pathlib import Path

from agent_policy_kit.paths import PathEscape, contained_join

from .errors import DestinationError


def safe_join(root: str, relpath: str) -> Path:
    try:
        return contained_join(root, relpath)
    except PathEscape as exc:
        raise DestinationError(str(exc)) from None
