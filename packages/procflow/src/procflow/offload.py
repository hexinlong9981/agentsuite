"""The ``offload`` adapter *interface*: hand one self-contained request to an external worker and wait.

procflow ships **no** real client. A deployment provides an :class:`OffloadClient` (an object with ``send``) and
installs it with :func:`set_client` -- or injects providers directly into the engine. Without a client an
``offload`` target is simply unavailable (the routing fallback chain moves on; nothing is ever called).

``send(request)`` takes ``{"mode": "llm" | "agent", "prompt": str, "model": str | None, "params": dict,
"schema": dict | None, "inputs": [path], "workdir": path | None, "timeout": seconds}`` and returns
``{"status": "ok" | "unavailable" | "quota" | "timeout" | "refused" | "failed" | "invalid_output",
"text": str | None, "cost": number | None, "model": str | None, "message": str | None}``.
``refused`` means the worker rejected the *content* (it is never retried on another target; the step goes to a
person). For ``mode: agent`` the worker may only write below ``<workdir>/output`` (the engine checks).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

ERROR_KINDS = ("unavailable", "quota", "timeout", "refused", "failed", "invalid_output")
_CLIENT = {"client": None}


class OffloadClient(ABC):
    @abstractmethod
    def send(self, request: dict) -> dict:
        """Run one request inline and return the response dict described in the module doc."""

    def available(self):
        """None when usable, else the reason it is not."""
        return None


def set_client(client):
    _CLIENT["client"] = client


def get_client():
    return _CLIENT["client"]


def available():
    c = get_client()
    if c is None:
        return "no offload client is installed (procflow ships none)"
    return c.available()


def send(request):
    c = get_client()
    if c is None:
        return {"status": "unavailable", "message": "no offload client is installed"}
    try:
        r = c.send(request)
    except Exception as x:  # noqa: BLE001 - a client bug must degrade to "unavailable", never crash the engine
        return {"status": "failed", "message": f"offload client raised {type(x).__name__}"}
    if not isinstance(r, dict) or r.get("status") not in ("ok",) + ERROR_KINDS:
        return {"status": "failed", "message": "offload client returned an unreadable response"}
    return r
