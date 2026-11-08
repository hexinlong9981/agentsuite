"""Freshness tracking: last_used / last_reviewed in a JSON sidecar keyed by item name."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from agent_policy_kit import contained_join

from .model import validate_name
from .store import KnowledgeBase

META_FILE = ".freshness.json"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class StaleEntry:
    name: str
    reasons: tuple[str, ...]
    last_used: str | None
    last_reviewed: str | None


class Freshness:
    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self.path = contained_join(str(kb.root), META_FILE)

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        self.kb.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _mark(self, name: str, key: str, now: datetime | None) -> str:
        validate_name(name)
        self.kb.get(name)  # must exist (raises ItemNotFound)
        data = self._load()
        stamp = _iso(now or datetime.now(timezone.utc))
        data.setdefault(name, {})[key] = stamp
        self._save(data)
        self.kb.audit.allow(op=key, name=name)
        return stamp

    def mark_used(self, name: str, now: datetime | None = None) -> str:
        return self._mark(name, "last_used", now)

    def mark_reviewed(self, name: str, now: datetime | None = None) -> str:
        return self._mark(name, "last_reviewed", now)

    def get(self, name: str) -> dict:
        return dict(self._load().get(name, {}))

    def staleness_report(self, now: datetime | None = None, *, used_days: int = 90,
                         reviewed_days: int = 180) -> list[StaleEntry]:
        """Shelf items not used within used_days or not reviewed within reviewed_days
        (never used / never reviewed counts as stale)."""
        now = now or datetime.now(timezone.utc)
        data = self._load()
        out = []
        for n in self.kb.names():
            rec = data.get(n, {})
            used, rev = _parse(rec.get("last_used")), _parse(rec.get("last_reviewed"))
            reasons = []
            if used is None:
                reasons.append("never-used")
            elif now - used > timedelta(days=used_days):
                reasons.append(f"unused>{used_days}d")
            if rev is None:
                reasons.append("never-reviewed")
            elif now - rev > timedelta(days=reviewed_days):
                reasons.append(f"unreviewed>{reviewed_days}d")
            if reasons:
                out.append(StaleEntry(n, tuple(reasons), rec.get("last_used"), rec.get("last_reviewed")))
        return out
