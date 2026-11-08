"""In-memory fake mail backend for demos and offline tests.

``folders`` shape::

    {
      "INBOX": {
        "messages": [{"id": "1", "subject": "...", "from": "...", "body": "..."}],
        "attachments": {("1", "a.txt"): b"bytes"},
      }
    }
"""

from __future__ import annotations

from ..errors import BackendError
from . import MailBackend


class FakeMailBackend(MailBackend):
    def __init__(self, folders: dict | None = None) -> None:
        self.folders = folders or {}

    def list_folders(self) -> list[str]:
        return sorted(self.folders)

    def list_messages(self, folder: str) -> list[dict]:
        return [{"id": m["id"], "subject": m.get("subject", ""), "from": m.get("from", "")}
                for m in self.folders.get(folder, {}).get("messages", [])]

    def read_message(self, folder: str, message_id: str) -> dict:
        for m in self.folders.get(folder, {}).get("messages", []):
            if m["id"] == message_id:
                return dict(m)
        raise BackendError(f"message not found: {message_id}")

    def search(self, folder: str, query: str) -> list[dict]:
        out = []
        for m in self.folders.get(folder, {}).get("messages", []):
            hay = f"{m.get('subject', '')} {m.get('body', '')} {m.get('from', '')}"
            if query in hay:
                out.append({"id": m["id"], "subject": m.get("subject", "")})
        return out

    def download_attachment(self, folder: str, message_id: str, name: str) -> bytes:
        att = self.folders.get(folder, {}).get("attachments", {})
        if (message_id, name) in att:
            return att[(message_id, name)]
        raise BackendError(f"attachment not found: {message_id}/{name}")
