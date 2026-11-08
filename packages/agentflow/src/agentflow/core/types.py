from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Event:
    ts: str
    type: str                 # step_start | step_end | note | tool | error | ...
    step: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class RunMeta:
    run_id: str
    flow: str = "default"
    started_at: str = ""
    finished_at: str | None = None
    status: str = "running"   # running | ok | error


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
