"""Recorder — append an agent's run as events (JSONL), secrets masked."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from agent_policy_kit import SecretMasker
from agent_policy_kit.paths import contained_join

from .types import RunMeta, utcnow_iso


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]


class Recorder:
    def __init__(self, root: str, *, flow: str = "default", run_id: str | None = None,
                 masker: SecretMasker | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or _new_run_id()
        self.flow = flow
        self.masker = masker or SecretMasker()
        self.dir = contained_join(str(self.root), self.run_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.meta = RunMeta(run_id=self.run_id, flow=flow, started_at=utcnow_iso())
        self._write_meta()

    def _write_meta(self) -> None:
        (self.dir / "meta.json").write_text(
            json.dumps(self.meta.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")

    def record(self, type: str, step: str = "", **data) -> dict:
        event = {"ts": utcnow_iso(), "type": type, "step": step,
                 "data": self.masker.mask_obj(data)}
        with open(self.events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def note(self, text: str, step: str = "") -> dict:
        return self.record("note", step=step, text=text)

    def tool(self, name: str, step: str = "", **data) -> dict:
        return self.record("tool", step=step, name=name, **data)

    def error(self, message: str, step: str = "") -> dict:
        return self.record("error", step=step, message=message)

    @contextmanager
    def step(self, name: str):
        self.record("step_start", step=name)
        try:
            yield self
        except Exception as exc:
            self.record("step_end", step=name, status="error", error=type(exc).__name__)
            raise
        else:
            self.record("step_end", step=name, status="ok")

    def finish(self, status: str = "ok") -> None:
        self.meta.finished_at = utcnow_iso()
        self.meta.status = status
        self._write_meta()
