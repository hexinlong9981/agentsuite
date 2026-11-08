"""Agent runtime layer: a worker that may write files, under a sandbox, and summarise.

Contract: the engine creates ``<workdir>/input`` and ``<workdir>/output``; the agent writes products **only**
below ``output/`` and its summary (a JSON object) to ``output/result.json``. The engine then checks the
sandbox (nothing outside ``output/``, no links, bounded size), validates the summary against the step's
output schema, and records file names + hashes (never contents). Backends (a registry entry's ``provider``):
session (a queued work ticket), offload (inline through an installed client), disabled, fake (scripted).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from agent_policy_kit import PathEscape, contained_join

from .. import fakeprov, offload

MAX_FILES = 200
MAX_TOTAL_BYTES = 20_000_000


@dataclass
class AgentRequest:
    prompt: str
    workdir: Path
    inputs: list = field(default_factory=list)
    model: str | None = None
    params: dict = field(default_factory=dict)
    needs: list = field(default_factory=list)
    step: str = ""
    attempt: int = 1
    timeout: int = 1800


@dataclass
class AgentResult:
    summary: dict | None = None
    model: str | None = None
    via: str | None = None
    cost: float | None = None
    outside_writes: list = field(default_factory=list)
    error: str | None = None
    error_kind: str | None = None

    @property
    def ok(self):
        return self.error is None


class Sandbox:
    """Write access limited to ``<workdir>/output`` (a path that leaves it raises PathEscape)."""

    def __init__(self, workdir):
        self.workdir = Path(workdir)
        self.output = self.workdir / "output"
        self.output.mkdir(parents=True, exist_ok=True)

    def write(self, rel, content):
        p = contained_join(str(self.output), rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return p


def collect(workdir):
    """Inspect a finished workdir. Returns ``{"files": [{path, bytes, sha256}], "summary", "problems"}``."""
    wd = Path(workdir)
    problems, files, total = [], [], 0
    for child in sorted(wd.iterdir()) if wd.is_dir() else []:
        if child.name not in ("input", "output"):
            problems.append(f"{child.name}: written outside the output directory")
    out = wd / "output"
    for p in sorted(out.rglob("*")) if out.is_dir() else []:
        rel = p.relative_to(out).as_posix()
        if p.is_symlink():
            problems.append(f"output/{rel}: a symbolic link is not allowed")
        elif p.is_file():
            n = p.stat().st_size
            total += n
            files.append({"path": rel, "bytes": n, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        elif not p.is_dir():
            problems.append(f"output/{rel}: not a regular file")
    if len(files) > MAX_FILES:
        problems.append(f"output has more than {MAX_FILES} files")
    if total > MAX_TOTAL_BYTES:
        problems.append(f"output is larger than {MAX_TOTAL_BYTES} bytes")
    summary = None
    rj = out / "result.json"
    if rj.is_file() and not rj.is_symlink():
        try:
            summary = json.loads(rj.read_text(encoding="utf-8"))
        except ValueError:
            problems.append("output/result.json is not valid JSON")
    return {"files": files, "summary": summary, "problems": problems}


class AgentRuntime:
    mode = "invoke"

    def __init__(self, name, entry):
        self.name, self.entry = name, entry

    def available(self):
        return None

    def run(self, req):  # pragma: no cover - interface
        raise NotImplementedError


class SessionAgent(AgentRuntime):
    mode = "ticket"

    def run(self, req):
        raise RuntimeError("a session target is never called by the engine (a work ticket is queued)")


class DisabledAgent(AgentRuntime):
    def available(self):
        return f"{self.name} is disabled ({self.entry.get('disabled_reason') or 'no reason given'})"

    def run(self, req):
        return AgentResult(error=self.available(), error_kind="unavailable", via="disabled")


class OffloadAgent(AgentRuntime):
    def available(self):
        return offload.available()

    def run(self, req):
        why = self.available()
        if why:
            return AgentResult(error=why, error_kind="unavailable", via="offload")
        r = offload.send({"mode": "agent", "prompt": req.prompt, "model": req.model, "params": req.params,
                          "schema": None, "inputs": [str(x) for x in req.inputs], "workdir": str(req.workdir),
                          "timeout": req.timeout})
        if r["status"] != "ok":
            return AgentResult(error=str(r.get("message") or r["status"])[:300], error_kind=r["status"], via="offload")
        cost = r.get("cost")
        return AgentResult(model=r.get("model") or req.model, via="offload",
                           cost=cost if isinstance(cost, (int, float)) and not isinstance(cost, bool)
                           else self.entry.get("cost_per_call"))


class FakeAgent(AgentRuntime):
    def run(self, req):
        rep = fakeprov.next_reply("agent", self.name, {"step": req.step, "attempt": req.attempt,
                                                        "prompt_sha256": fakeprov.sha256_text(req.prompt)},
                                  inline=self.entry.get("script"))
        if "error" in rep:
            return AgentResult(error=f"fake: {rep.get('message') or rep['error']}", error_kind=rep["error"], via="fake")
        sb = Sandbox(req.workdir)
        outside = []
        for name, body in (rep.get("files") or {}).items():
            try:
                sb.write(name, body)
            except PathEscape:
                outside.append(str(name))
        if "summary" in rep:
            sb.write("result.json", rep["summary"])
        for name in rep.get("outside") or []:
            try:
                sb.write(name, "x")
            except PathEscape:
                outside.append(str(name))
        for name in rep.get("stray") or []:  # simulate a misbehaving worker that writes beside the sandbox
            (Path(req.workdir) / contained_name(name)).write_text("x", encoding="utf-8")
        return AgentResult(model=rep.get("model") or req.model, via="fake", cost=rep.get("cost", self.entry.get("cost_per_call")),
                           outside_writes=outside)


def contained_name(name):
    return os.path.basename(str(name)) or "stray.txt"


IMPLS = {"session": SessionAgent, "disabled": DisabledAgent, "offload": OffloadAgent, "fake": FakeAgent}


def runtime(name, entry):
    return IMPLS.get(entry.get("provider"), DisabledAgent)(name, entry)
