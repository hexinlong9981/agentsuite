from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class FetchedFile:
    path: str          # path relative to the source root
    category: str
    dest: str          # absolute local destination
    bytes: int


@dataclass(frozen=True)
class SyncResult:
    policy: str
    source: str
    fetched: list[FetchedFile] = field(default_factory=list)
    skipped: int = 0
    total_bytes: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
