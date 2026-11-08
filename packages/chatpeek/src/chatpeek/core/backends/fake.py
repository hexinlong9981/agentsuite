"""In-memory fake chat backend for demos and offline tests.

``channels`` shape::

    {
      "general": {
        "messages": [{"id": "1", "author": "...", "text": "..."}],
        "files": {("1", "a.txt"): b"bytes"},
      }
    }
"""

from __future__ import annotations

from ..errors import BackendError
from . import ChatBackend


class FakeChatBackend(ChatBackend):
    def __init__(self, channels: dict | None = None) -> None:
        self.channels = channels or {}
        self.posts: dict[str, list[str]] = {}

    def list_channels(self) -> list[str]:
        return sorted(self.channels)

    def list_messages(self, channel: str) -> list[dict]:
        return [{"id": m["id"], "author": m.get("author", ""), "text": m.get("text", "")[:80]}
                for m in self.channels.get(channel, {}).get("messages", [])]

    def read_message(self, channel: str, message_id: str) -> dict:
        for m in self.channels.get(channel, {}).get("messages", []):
            if m["id"] == message_id:
                return dict(m)
        raise BackendError(f"message not found: {message_id}")

    def search(self, channel: str, query: str) -> list[dict]:
        out = []
        for m in self.channels.get(channel, {}).get("messages", []):
            hay = f"{m.get('text', '')} {m.get('author', '')}"
            if query in hay:
                out.append({"id": m["id"], "author": m.get("author", "")})
        return out

    def download_file(self, channel: str, message_id: str, name: str) -> bytes:
        files = self.channels.get(channel, {}).get("files", {})
        if (message_id, name) in files:
            return files[(message_id, name)]
        raise BackendError(f"file not found: {message_id}/{name}")

    def post_message(self, channel: str, text: str) -> dict:
        self.posts.setdefault(channel, []).append(text)
        return {"posted": channel}
