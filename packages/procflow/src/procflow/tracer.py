"""Pluggable observability bridge: step / checkpoint events to an external tracer.

Only ids, counters and hashes are ever sent -- never values. :func:`sanitize` enforces that for every
implementation (unknown keys are dropped; values must be short scalars).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

ALLOWED_KEYS = {"run_id", "sop", "step", "node", "round", "attempt", "status", "result", "seq", "state_sha256",
                "kind", "ticket", "reason", "count", "token"}
MAX_STR = 200


def sanitize(fields):
    out = {}
    for k, v in fields.items():
        if k not in ALLOWED_KEYS:
            continue
        if isinstance(v, (bool, int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:MAX_STR]
    return out


class Tracer(ABC):
    @abstractmethod
    def emit(self, event: str, **fields) -> None:
        """Record ``event`` ("run_start", "step_start", "step_end", "checkpoint", "run_end")."""

    def event(self, event, **fields):
        self.emit(event, **sanitize(fields))


class NoopTracer(Tracer):
    def emit(self, event, **fields):
        return None


class FakeTracer(Tracer):
    """Keeps events in memory (tests)."""

    def __init__(self):
        self.events = []

    def emit(self, event, **fields):
        self.events.append({"event": event, **fields})


class JsonlTracer(Tracer):
    def __init__(self, path):
        self.path = Path(path)

    def emit(self, event, **fields):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n")
        except OSError:
            pass  # observability must never stop a run


def get_tracer(spec):
    """``None`` / "noop" / "fake" / "jsonl:<path>" -> a tracer."""
    if spec in (None, "", "noop"):
        return NoopTracer()
    if spec == "fake":
        return FakeTracer()
    if isinstance(spec, str) and spec.startswith("jsonl:") and len(spec) > 6:
        return JsonlTracer(spec[6:])
    raise ValueError(f"unknown tracer {spec!r} (noop | fake | jsonl:<path>)")


class PrefixTracer(Tracer):
    """A child run's view of its root's tracer: step ids get a ``<call>-`` prefix, and the child has no top-level
    ``run_start`` / ``run_end`` of its own (it is a part of the parent's trace, not a separate one)."""

    def __init__(self, inner, prefix):
        self.inner = inner.inner if isinstance(inner, PrefixTracer) else inner
        self.prefix = prefix

    def emit(self, event, **fields):
        if event in ("run_start", "run_end"):
            return None
        if fields.get("step"):
            fields = dict(fields, step=f"{self.prefix}{fields['step']}")
        self.inner.event(event, **fields)
