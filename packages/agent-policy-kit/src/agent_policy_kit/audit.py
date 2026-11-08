"""Append-only JSON-lines audit log. Records allows and denials (with reason)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditLog:
    def __init__(self, path: str | None = None) -> None:
        self.path = path

    def record(self, event: dict) -> None:
        if not self.path:
            return
        rec = {"ts": _utcnow(), **event}
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def allow(self, **event) -> None:
        self.record({"result": "allow", **event})

    def deny(self, reason: str, **event) -> None:
        self.record({"result": "deny", "reason": reason, **event})
