"""RunStore — list and load runs (contained under the root)."""

from __future__ import annotations

import json
from pathlib import Path

from agent_policy_kit.paths import contained_join

from .errors import RunNotFound
from .types import Event, RunMeta

_META_KEYS = ("run_id", "flow", "started_at", "finished_at", "status")


def _meta_from(d: dict) -> RunMeta:
    return RunMeta(**{k: d.get(k) for k in _META_KEYS if d.get(k) is not None})


class RunStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root).expanduser().resolve()

    def list_runs(self) -> list[RunMeta]:
        out: list[RunMeta] = []
        if not self.root.is_dir():
            return out
        for d in sorted(self.root.iterdir()):
            meta_path = d / "meta.json"
            if d.is_dir() and meta_path.is_file():
                try:
                    out.append(_meta_from(json.loads(meta_path.read_text(encoding="utf-8"))))
                except (OSError, ValueError, TypeError):
                    continue
        return out

    def load(self, run_id: str) -> tuple[RunMeta, list[Event]]:
        d = contained_join(str(self.root), run_id)
        meta_path = d / "meta.json"
        if not meta_path.is_file():
            raise RunNotFound(f"no such run: {run_id}")
        meta = _meta_from(json.loads(meta_path.read_text(encoding="utf-8")))
        events: list[Event] = []
        events_path = d / "events.jsonl"
        if events_path.is_file():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                events.append(Event(ts=e.get("ts", ""), type=e.get("type", ""),
                                    step=e.get("step", ""), data=e.get("data", {})))
        return meta, events
