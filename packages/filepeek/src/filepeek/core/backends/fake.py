"""In-memory fake file store backend for demos and offline tests.

``folders`` shape::

    {
      "docs": {
        "files": [{"id": "1", "name": "a.txt", "description": "..."}],
        "contents": {"1": b"bytes"},
      }
    }
"""

from __future__ import annotations

from ..errors import BackendError
from . import FileStoreBackend


class FakeFileStoreBackend(FileStoreBackend):
    def __init__(self, folders: dict | None = None) -> None:
        self.folders = folders or {}
        self.uploads: list[tuple[str, str, bytes]] = []

    def list_folders(self) -> list[str]:
        return sorted(self.folders)

    def list_files(self, folder: str) -> list[dict]:
        return [{"id": f["id"], "name": f.get("name", "")}
                for f in self.folders.get(folder, {}).get("files", [])]

    def read_file(self, folder: str, file_id: str) -> dict:
        for f in self.folders.get(folder, {}).get("files", []):
            if f["id"] == file_id:
                return dict(f)
        raise BackendError(f"file not found: {file_id}")

    def search(self, folder: str, query: str) -> list[dict]:
        out = []
        for f in self.folders.get(folder, {}).get("files", []):
            hay = f"{f.get('name', '')} {f.get('description', '')}"
            if query in hay:
                out.append({"id": f["id"], "name": f.get("name", "")})
        return out

    def download(self, folder: str, file_id: str) -> bytes:
        contents = self.folders.get(folder, {}).get("contents", {})
        if file_id in contents:
            return contents[file_id]
        raise BackendError(f"file not found: {file_id}")

    def upload(self, folder: str, name: str, data: bytes) -> dict:
        self.uploads.append((folder, name, data))
        return {"uploaded": name}
