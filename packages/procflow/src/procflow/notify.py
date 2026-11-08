"""Event notification engine for procflow.

Records run lifecycle events, revision alerts, and scheduler triggers to an append-only
log (_notify.jsonl) while masking sensitive values.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_policy_kit import SecretMasker


EVENT_KINDS = (
    "failed",
    "suspended",
    "resumed",
    "completed",
    "auto_resume",
    "auto_resume_limit",
    "revision_applied",
    "revision_rejected",
    "schedule_triggered",
    "schedule_error",
)


@dataclass
class NotificationEvent:
    event_id: str
    timestamp: str
    kind: str
    sop_id: str | None = None
    run_id: str | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "ts": self.timestamp,
            "kind": self.kind,
            "sop": self.sop_id,
            "run_id": self.run_id,
            "message": self.message,
            "details": self.details,
        }


def get_notify_file(runs_root: Path | str) -> Path:
    return Path(runs_root).resolve() / "_notify.jsonl"


def notify(
    runs_root: Path | str,
    kind: str,
    message: str,
    sop_id: str | None = None,
    run_id: str | None = None,
    details: dict[str, Any] | None = None,
    timestamp: str | None = None,
    masker: SecretMasker | None = None,
) -> NotificationEvent:
    """Record a masked notification event to _notify.jsonl."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    sm = masker or SecretMasker()
    clean_msg = sm.mask_text(str(message))[:1000]

    raw_id_src = f"{ts}|{kind}|{run_id or ''}|{clean_msg}"
    event_id = hashlib.sha1(raw_id_src.encode("utf-8")).hexdigest()[:12]

    clean_details: dict[str, Any] = {}
    if details:
        for k, v in details.items():
            if isinstance(v, str):
                clean_details[k] = sm.mask_text(v)
            else:
                clean_details[k] = v

    event = NotificationEvent(
        event_id=event_id,
        timestamp=ts,
        kind=kind,
        sop_id=sop_id,
        run_id=run_id,
        message=clean_msg,
        details=clean_details,
    )

    p = get_notify_file(runs_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event.to_dict()) + "\n")

    return event


def read_notifications(
    runs_root: Path | str,
    kinds: Iterable[str] | None = None,
    sop_id: str | None = None,
    run_id: str | None = None,
    limit: int | None = None,
) -> list[NotificationEvent]:
    """Read notification events from _notify.jsonl with filtering."""
    p = get_notify_file(runs_root)
    if not p.exists():
        return []

    target_kinds = set(kinds) if kinds else None
    events: list[NotificationEvent] = []

    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                k = d.get("kind")
                s = d.get("sop")
                r = d.get("run_id")

                if target_kinds and k not in target_kinds:
                    continue
                if sop_id and s != sop_id:
                    continue
                if run_id and r != run_id:
                    continue

                events.append(
                    NotificationEvent(
                        event_id=d.get("id", ""),
                        timestamp=d.get("ts", ""),
                        kind=k or "unknown",
                        sop_id=s,
                        run_id=r,
                        message=d.get("message", ""),
                        details=d.get("details", {}),
                    )
                )
    except Exception:
        pass

    if limit and len(events) > limit:
        return events[-limit:]
    return events
