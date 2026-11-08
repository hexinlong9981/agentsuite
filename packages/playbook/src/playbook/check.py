"""Self-check of the principle set, and an audit of a guidelines document."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .principles import PRINCIPLES, TAGS, Principle


@dataclass
class CheckResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def check(principles: Iterable[Principle] | None = None) -> CheckResult:
    items = list(PRINCIPLES if principles is None else principles)
    problems: list[str] = []
    seen: set[str] = set()
    for i, p in enumerate(items):
        label = p.id or f"#{i}"
        for name in ("id", "title", "rule", "rationale"):
            if not str(getattr(p, name, "")).strip():
                problems.append(f"{label}: empty {name}")
        if p.id in seen:
            problems.append(f"{label}: duplicate id")
        seen.add(p.id)
        if p.id and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", p.id):
            problems.append(f"{label}: id must be kebab-case")
        if not p.applies_to:
            problems.append(f"{label}: applies_to is empty")
        for t in p.applies_to:
            if t not in TAGS:
                problems.append(f"{label}: unknown tag {t!r}")
    return CheckResult(not problems, problems)


@dataclass
class AuditResult:
    referenced: list[str]
    missing: list[str]

    @property
    def ok(self) -> bool:
        return not self.missing


def audit_text(text: str, principles: Iterable[Principle] | None = None) -> AuditResult:
    """Report which principle ids are referenced (by id or title) in ``text``."""
    items = list(PRINCIPLES if principles is None else principles)
    low = text.lower()
    referenced, missing = [], []
    for p in items:
        hit = re.search(r"(?<![\w-])" + re.escape(p.id) + r"(?![\w-])", low) \
            or p.title.lower() in low
        (referenced if hit else missing).append(p.id)
    return AuditResult(referenced, missing)
