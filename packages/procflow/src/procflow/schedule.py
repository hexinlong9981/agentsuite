"""Scheduled execution definition and cron evaluation for procflow procedures.

Parses 5-field cron expressions, validates schedule configurations, and computes
next execution times without external dependencies.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class CronParseError(ValueError):
    pass


_FIELD_RANGES = [
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),   # day of week (0 and 7 are Sunday)
]


def _parse_field(field_str: str, lo: int, hi: int) -> set[int]:
    """Parse a single cron field part into a set of integers."""
    result: set[int] = set()
    parts = field_str.split(",")

    for part in parts:
        step = 1
        if "/" in part:
            sub, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise CronParseError(f"Invalid cron step: {step_s}")
            step = int(step_s)
            part = sub

        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            p1, p2 = part.split("-", 1)
            if not (p1.isdigit() and p2.isdigit()):
                raise CronParseError(f"Invalid cron range: {part}")
            start, end = int(p1), int(p2)
            if start < lo or end > hi or start > end:
                raise CronParseError(f"Cron range out of bounds ({lo}-{hi}): {part}")
        elif part.isdigit():
            val = int(part)
            if val < lo or val > hi:
                raise CronParseError(f"Cron value {val} out of bounds ({lo}-{hi})")
            start, end = val, val
        else:
            raise CronParseError(f"Unrecognized cron token: {part}")

        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                result.add(v)

    # Normalize Sunday 7 -> 0 for weekday field
    if lo == 0 and hi == 7 and 7 in result:
        result.remove(7)
        result.add(0)

    return result


@dataclass
class CronSchedule:
    raw: str
    minutes: set[int]
    hours: set[int]
    days: set[int]
    months: set[int]
    weekdays: set[int]

    @classmethod
    def parse(cls, cron_expr: str) -> CronSchedule:
        fields = cron_expr.strip().split()
        if len(fields) != 5:
            raise CronParseError(f"Cron expression must contain exactly 5 fields, got {len(fields)}: {cron_expr}")

        minutes = _parse_field(fields[0], 0, 59)
        hours = _parse_field(fields[1], 0, 23)
        days = _parse_field(fields[2], 1, 31)
        months = _parse_field(fields[3], 1, 12)
        weekdays = _parse_field(fields[4], 0, 7)

        return cls(
            raw=cron_expr,
            minutes=minutes,
            hours=hours,
            days=days,
            months=months,
            weekdays=weekdays,
        )

    def matches(self, t: dt.datetime) -> bool:
        """Check if a given datetime matches this cron schedule."""
        # Convert Python weekday (Monday=0 ... Sunday=6) to cron (Sunday=0, Monday=1 ... Saturday=6)
        cron_wday = (t.weekday() + 1) % 7
        return (
            t.minute in self.minutes
            and t.hour in self.hours
            and t.day in self.days
            and t.month in self.months
            and cron_wday in self.weekdays
        )

    def next_fire_time(self, after_dt: dt.datetime | None = None, max_days: int = 366) -> dt.datetime | None:
        """Calculate the next matching datetime starting from after_dt."""
        start = after_dt or dt.datetime.now(dt.timezone.utc)
        curr = start.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        limit = curr + dt.timedelta(days=max_days)

        while curr <= limit:
            cron_wday = (curr.weekday() + 1) % 7
            if curr.month not in self.months:
                # Skip to beginning of next month
                if curr.month == 12:
                    curr = curr.replace(year=curr.year + 1, month=1, day=1, hour=0, minute=0)
                else:
                    curr = curr.replace(month=curr.month + 1, day=1, hour=0, minute=0)
                continue

            if curr.day not in self.days or cron_wday not in self.weekdays:
                # Advance day
                curr = (curr + dt.timedelta(days=1)).replace(hour=0, minute=0)
                continue

            if curr.hour not in self.hours:
                # Advance hour
                curr = (curr + dt.timedelta(hours=1)).replace(minute=0)
                continue

            if curr.minute in self.minutes:
                return curr

            curr += dt.timedelta(minutes=1)

        return None


@dataclass
class ScheduleEntry:
    schedule_id: str
    sop_id: str
    cron_expr: str
    enabled: bool = False
    overlap: str = "skip"  # "skip" or "queue"
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.schedule_id,
            "sop": self.sop_id,
            "cron": self.cron_expr,
            "enabled": self.enabled,
            "overlap": self.overlap,
            "params": self.parameters,
            "description": self.description,
        }


def load_schedules(path: Path | str) -> list[ScheduleEntry]:
    """Load schedule configurations from a YAML file."""
    p = Path(path)
    if not p.exists():
        return []

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entries = []

    schedules_raw = data.get("schedules", []) if isinstance(data, dict) else data
    for item in schedules_raw:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id", ""))
        sop_id = str(item.get("sop", ""))
        cron_expr = str(item.get("cron", ""))
        if not sid or not sop_id or not cron_expr:
            continue
        entries.append(
            ScheduleEntry(
                schedule_id=sid,
                sop_id=sop_id,
                cron_expr=cron_expr,
                enabled=bool(item.get("enabled", False)),
                overlap=str(item.get("overlap", "skip")),
                parameters=item.get("params", {}),
                description=str(item.get("description", "")),
            )
        )

    return entries
