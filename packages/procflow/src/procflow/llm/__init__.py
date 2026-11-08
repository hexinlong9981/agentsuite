"""LLM service layer: one self-contained call that returns text (JSON when a schema is given).

The engine owns checking, retry-with-reason, fallback, escalation and records (aistep.py). A service only
answers. Backends (a registry entry's ``provider``):

  session   an interactive worker session -- never called by the engine; a work ticket is queued (mode ``ticket``)
  offload   :mod:`procflow.offload` -- runs inline through an installed client (none ships)
  disabled  unusable; every call is ``unavailable``
  fake      scripted replies (:mod:`procflow.fakeprov`); only with ``PROCFLOW_ALLOW_FAKE_PROVIDERS=1``
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .. import fakeprov, offload


@dataclass
class LlmRequest:
    prompt: str
    model: str | None = None
    params: dict = field(default_factory=dict)
    schema: dict | None = None
    step: str = ""
    attempt: int = 1
    data_class: str | None = None
    timeout: int = 600


@dataclass
class LlmResult:
    text: str | None = None
    model: str | None = None
    via: str | None = None
    cost: float | None = None
    error: str | None = None
    error_kind: str | None = None

    @property
    def ok(self):
        return self.error is None


class LlmService:
    mode = "invoke"           # invoke: the engine calls it | ticket: a work ticket is queued for a session

    def __init__(self, name, entry):
        self.name, self.entry = name, entry

    def available(self):
        return None

    def call(self, req):  # pragma: no cover - interface
        raise NotImplementedError


class SessionLlm(LlmService):
    mode = "ticket"

    def call(self, req):
        raise RuntimeError("a session target is never called by the engine (a work ticket is queued)")


class DisabledLlm(LlmService):
    def available(self):
        return f"{self.name} is disabled ({self.entry.get('disabled_reason') or 'no reason given'})"

    def call(self, req):
        return LlmResult(error=self.available(), error_kind="unavailable", via="disabled")


class OffloadLlm(LlmService):
    def available(self):
        return offload.available()

    def call(self, req):
        why = self.available()
        if why:
            return LlmResult(error=why, error_kind="unavailable", via="offload")
        r = offload.send({"mode": "llm", "prompt": req.prompt, "model": req.model, "params": req.params,
                          "schema": req.schema, "inputs": [], "workdir": None, "timeout": req.timeout})
        if r["status"] != "ok":
            return LlmResult(error=str(r.get("message") or r["status"])[:300], error_kind=r["status"], via="offload")
        cost = r.get("cost")
        return LlmResult(text=r.get("text"), model=r.get("model") or req.model, via="offload",
                         cost=cost if isinstance(cost, (int, float)) and not isinstance(cost, bool)
                         else self.entry.get("cost_per_call"))


class FakeLlm(LlmService):
    def call(self, req):
        rep = fakeprov.next_reply("llm", self.name, {"step": req.step, "attempt": req.attempt,
                                                      "prompt_sha256": fakeprov.sha256_text(req.prompt),
                                                      "model": req.model}, inline=self.entry.get("script"))
        if "error" in rep:
            return LlmResult(error=f"fake: {rep.get('message') or rep['error']}", error_kind=rep["error"], via="fake")
        text = rep.get("text")
        if text is None and "output" in rep:
            text = json.dumps(rep["output"], ensure_ascii=False)
        return LlmResult(text=text, model=rep.get("model") or req.model, via="fake",
                         cost=rep.get("cost", self.entry.get("cost_per_call")))


IMPLS = {"session": SessionLlm, "disabled": DisabledLlm, "offload": OffloadLlm, "fake": FakeLlm}


def service(name, entry):
    return IMPLS.get(entry.get("provider"), DisabledLlm)(name, entry)
