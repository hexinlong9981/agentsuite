"""Data types returned by the core API. None of these carry secret values."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def citation(repo: str, sha: str, path: str, line: int | None = None) -> str:
    """Build a citation string: ``repo@sha : path`` or ``repo@sha : path:line``.

    Because an ephemeral checkout is discarded after use, every result is
    quoted in this form so the evidence can still be located later.
    """
    base = f"{repo}@{sha} : {path}"
    return f"{base}:{line}" if line is not None else base


@dataclass(frozen=True)
class FetchRequest:
    repo: str                      # allowlist key, path, or URL
    ref: str | None = None         # branch/tag/sha; None = default
    mode: str = "auto"             # auto | mirror | direct
    full_history: bool = True


@dataclass(frozen=True)
class Checkout:
    id: str
    repo: str
    ref: str
    path: str
    commit_sha: str
    commits_available: int
    created_at: str                # ISO 8601 UTC
    provenance_path: str

    @property
    def citation_base(self) -> str:
        return f"{self.repo}@{self.commit_sha}"


@dataclass(frozen=True)
class MaskReport:
    enabled: bool
    action: str                    # mask | delete | fail
    files_scanned: int = 0
    hits: int = 0
    files_masked: int = 0
    files_deleted: int = 0


@dataclass(frozen=True)
class FetchResult:
    checkout: Checkout
    citation_base: str             # "<repo>@<sha>"
    mask: MaskReport
    push_guard: str                # "enabled" | "disabled"
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReleaseResult:
    released: list[str] = field(default_factory=list)   # checkout ids


@dataclass(frozen=True)
class PurgeResult:
    purged: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)       # foreign dirs left alone


@dataclass(frozen=True)
class Status:
    repo: str
    checkouts: list[Checkout] = field(default_factory=list)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
