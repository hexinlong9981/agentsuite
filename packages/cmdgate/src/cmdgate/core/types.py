from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class CommandResult:
    target: str
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False
    allowed_by: str = ""     # "policy:program"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    policy: str = ""
    program: str = ""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
