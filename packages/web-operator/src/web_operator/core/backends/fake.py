"""In-memory fake backend for demos and offline tests.

``pages`` maps a url to ``{"text": str, "elements": list, "download": bytes}``.
Interactions are recorded in ``actions`` so tests can assert what ran.
"""

from __future__ import annotations

from ..errors import BackendError
from . import BrowserBackend


class FakeBackend(BrowserBackend):
    def __init__(self, pages: dict | None = None) -> None:
        self.pages = pages or {}
        self.current: str | None = None
        self.actions: list[tuple[str, dict]] = []

    def dispatch(self, operation: str, **params):
        self.actions.append((operation, params))
        page = self.pages.get(self.current, {})
        if operation == "navigate":
            self.current = params.get("url")
            return {"url": self.current}
        if operation == "read":
            return page.get("text", "")
        if operation == "get_elements":
            return list(page.get("elements", []))
        if operation == "screenshot":
            return b"FAKEPNG"
        if operation == "download":
            return page.get("download", b"")
        if operation in ("fill", "select", "click"):
            return {"ok": True, "selector": params.get("selector")}
        if operation == "submit":
            return {"submitted": True, "selector": params.get("selector")}
        raise BackendError(f"unsupported operation {operation!r}")

    def describe(self, operation: str, **params) -> str:
        return (f"{operation} on {self.current} "
                f"selector={params.get('selector')} value={params.get('value')}")
