"""Output containment — CSV files stay under the configured output directory.

Uses the shared core (agent_policy_kit.paths) and maps its error to this tool's
``OutputError`` so callers/tests keep their own exception type.
"""

from __future__ import annotations

from pathlib import Path

from agent_policy_kit.paths import PathEscape, contained_join
from agent_policy_kit.paths import safe_name as _safe_name

from .errors import OutputError


def safe_name(name: str) -> str:
    return _safe_name(name, default="metric")


def safe_join(out_dir: str, filename: str) -> Path:
    try:
        return contained_join(out_dir, safe_name(filename))
    except PathEscape as exc:
        raise OutputError(str(exc)) from None
